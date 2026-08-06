"""Enrichissement cadastre à l'ingestion.

Stratégie :
1. Probe bbox (enveloppe UG + marge ~80 m)
2. matched ≤ ~5k → download direct
3. ~5k < matched ≤ 20k → carroyage adaptatif (quadtree) + dédup idu
4. matched > 20k → repli buffer 200 m par UG
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

import psycopg
from shapely import wkt
from shapely.ops import unary_union

from api.db.env import get_database_url

from .ign_client import (
    BBOX_MARGIN_M,
    DEFAULT_BUFFER_M,
    FALLBACK_BUFFER_M,
    LIMIT_CAP_TUILE,
    MAX_PARCELLES_ZONE,
    CadastreIgnError,
    CadastreTooManyError,
    fetch_parcelles_autour_geom_3857,
    fetch_parcelles_bbox_projet_3857,
)
from .persist import lier_parcelles_aux_ugs, remplacer_parcelles_ug, supprimer_cadastre_projet

logger = logging.getLogger(__name__)


def _croiser_ugs(projet_id: UUID, result: EnrichissementCadastreResult) -> None:
    """Matérialise les parcelles qui composent chaque UG (ST_Intersects)."""
    try:
        counts = lier_parcelles_aux_ugs(projet_id)
        total = sum(counts.values())
        result.details.append({
            "etape": "croisement_ug",
            "nb_liens": total,
            "par_ug": counts,
        })
        logger.info(
            "[cadastre] croisement UG↔parcelles | projet=%s | liens=%s | ugs=%s",
            projet_id,
            total,
            sorted(counts.keys()),
        )
    except Exception as exc:  # noqa: BLE001
        msg = f"croisement UG↔parcelles impossible: {exc}"
        logger.warning("[cadastre] %s", msg)
        result.avertissements.append(msg)

# Clé artificielle pour le snapshot « zone projet » (méthode bbox)
UG_ID_PROJET = "__projet__"

_UG_TABLES = (
    "unites_de_gestion_surf",
    "unites_de_gestion_lin",
    "unites_de_gestion_pct",
)

MethodeCadastre = Literal["bbox_projet", "bbox_carroyage", "buffer_ug"]


@dataclass
class EnrichissementCadastreResult:
    projet_id: str
    buffer_m: float
    methode: MethodeCadastre = "bbox_projet"
    methode_raison: str = ""
    number_matched_premier_appel: int | None = None
    ugs_traitees: list[str] = field(default_factory=list)
    nb_parcelles: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projet_id": self.projet_id,
            "buffer_m": self.buffer_m,
            "methode": self.methode,
            "methode_raison": self.methode_raison,
            "number_matched_premier_appel": self.number_matched_premier_appel,
            "ugs_traitees": self.ugs_traitees,
            "nb_parcelles": self.nb_parcelles,
            "details": self.details,
            "avertissements": self.avertissements,
        }


def _charger_unions_ug_3857(
    projet_id: UUID,
    *,
    ug_ids: list[str] | None = None,
) -> dict[str, Any]:
    """ug_id → géométrie 3857 (union des parties)."""
    out: dict[str, list] = {}
    with psycopg.connect(get_database_url()) as conn:
        with conn.cursor() as cur:
            for table in _UG_TABLES:
                if ug_ids:
                    cur.execute(
                        f"""
                        SELECT ug_id, ST_AsText(geom_3857)
                        FROM bancarisation.{table}
                        WHERE projet_id = %s
                          AND ug_id = ANY(%s)
                          AND geom_3857 IS NOT NULL
                        """,
                        (str(projet_id), ug_ids),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT ug_id, ST_AsText(geom_3857)
                        FROM bancarisation.{table}
                        WHERE projet_id = %s
                          AND geom_3857 IS NOT NULL
                          AND ug_id IS NOT NULL
                          AND ug_id <> ''
                        """,
                        (str(projet_id),),
                    )
                for ug_id, wkt_txt in cur.fetchall():
                    if not ug_id or not wkt_txt:
                        continue
                    try:
                        g = wkt.loads(wkt_txt)
                    except Exception:  # noqa: BLE001
                        continue
                    out.setdefault(str(ug_id), []).append(g)

    unions: dict[str, Any] = {}
    for ug_id, geoms in out.items():
        valid = [g for g in geoms if g is not None and not g.is_empty]
        if not valid:
            continue
        unions[ug_id] = unary_union(valid) if len(valid) > 1 else valid[0]
    return unions


def _enrichir_par_ug(
    projet_id: UUID,
    unions: dict[str, Any],
    *,
    buffer_m: float,
    result: EnrichissementCadastreResult,
    raison: str,
) -> EnrichissementCadastreResult:
    """Méthode historique : buffer autour de chaque UG."""
    result.methode = "buffer_ug"
    result.buffer_m = float(buffer_m)
    result.methode_raison = raison
    logger.info(
        "[cadastre] méthode=buffer_ug | buffer_m=%s | nb_ugs=%s | raison=%s | "
        "number_matched_premier_appel=%s",
        buffer_m,
        len(unions),
        raison,
        result.number_matched_premier_appel,
    )
    # Nettoie l'éventuel snapshot bbox projet
    supprimer_cadastre_projet(projet_id, ug_ids=[UG_ID_PROJET])

    for ug_id, geom_3857 in sorted(unions.items()):
        try:
            features = fetch_parcelles_autour_geom_3857(
                geom_3857,
                buffer_m=buffer_m,
                max_matched=None,  # pas de plafond sur le repli local
                label=f"buffer_ug:{ug_id}",
            )
            n = remplacer_parcelles_ug(
                projet_id=projet_id,
                ug_id=ug_id,
                features=features,
                buffer_m=buffer_m,
            )
            result.ugs_traitees.append(ug_id)
            result.nb_parcelles += n
            result.details.append({"ug_id": ug_id, "nb_parcelles": n})
        except CadastreIgnError as exc:
            msg = f"UG {ug_id}: {exc}"
            logger.warning("cadastre enrichissement buffer_ug: %s", msg)
            result.avertissements.append(msg)
            result.details.append({"ug_id": ug_id, "nb_parcelles": 0, "erreur": str(exc)})
        except Exception as exc:  # noqa: BLE001
            msg = f"UG {ug_id}: erreur inattendue ({exc})"
            logger.exception("cadastre enrichissement buffer_ug")
            result.avertissements.append(msg)
            result.details.append({"ug_id": ug_id, "nb_parcelles": 0, "erreur": str(exc)})

    logger.info(
        "[cadastre] buffer_ug terminé | nb_parcelles_total=%s | ugs=%s",
        result.nb_parcelles,
        result.ugs_traitees,
    )
    _croiser_ugs(projet_id, result)
    return result


def enrichir_cadastre_projet(
    projet_id: UUID,
    *,
    buffer_m: float = DEFAULT_BUFFER_M,
    ug_ids: list[str] | None = None,
    force_buffer_ug: bool = False,
) -> EnrichissementCadastreResult:
    """Enrichit le cadastre du projet.

    1. Probe bbox (enveloppe UG + marge)
    2. matched ≤ ~5k → download direct
    3. ~5k < matched ≤ 20k → carroyage adaptatif + dédup
    4. matched > 20k → repli buffer 200 m par UG
    """
    result = EnrichissementCadastreResult(
        projet_id=str(projet_id),
        buffer_m=float(buffer_m),
    )
    unions = _charger_unions_ug_3857(projet_id, ug_ids=ug_ids)
    if not unions:
        result.avertissements.append("Aucune UG géométrique trouvée pour enrichissement cadastre.")
        logger.warning("[cadastre] projet=%s — aucune UG géométrique", projet_id)
        return result

    logger.info(
        "[cadastre] projet=%s — démarrage enrichissement | nb_ugs=%s | ug_ids=%s | force_buffer_ug=%s",
        projet_id,
        len(unions),
        sorted(unions.keys()),
        force_buffer_ug,
    )

    if force_buffer_ug:
        use_buffer = FALLBACK_BUFFER_M if buffer_m == DEFAULT_BUFFER_M else buffer_m
        return _enrichir_par_ug(
            projet_id,
            unions,
            buffer_m=use_buffer,
            result=result,
            raison="force_buffer_ug=True",
        )

    all_geoms = list(unions.values())
    emprise = unary_union(all_geoms) if len(all_geoms) > 1 else all_geoms[0]
    env = emprise.envelope
    logger.info(
        "[cadastre] tentative bbox_projet | margin_m=%s | envelope_3857=%s | "
        "cap_tuile=%s | cap_zone=%s",
        BBOX_MARGIN_M,
        tuple(round(c, 1) for c in env.bounds),
        LIMIT_CAP_TUILE,
        MAX_PARCELLES_ZONE,
    )

    try:
        features, matched, sous_methode = fetch_parcelles_bbox_projet_3857(
            emprise, margin_m=BBOX_MARGIN_M,
        )
        result.number_matched_premier_appel = matched
    except CadastreTooManyError as exc:
        result.number_matched_premier_appel = exc.matched
        raison = (
            f"numberMatched={exc.matched} > limite_zone={exc.limit} "
            f"sur probe bbox → repli buffer {FALLBACK_BUFFER_M:.0f}m/UG"
        )
        logger.warning("[cadastre] %s", raison)
        result.avertissements.append(raison)
        return _enrichir_par_ug(
            projet_id,
            unions,
            buffer_m=FALLBACK_BUFFER_M,
            result=result,
            raison=raison,
        )
    except CadastreIgnError as exc:
        raison = (
            f"erreur IGN sur bbox : {exc} "
            f"→ repli buffer {FALLBACK_BUFFER_M:.0f}m/UG"
        )
        logger.warning("[cadastre] %s", raison)
        result.avertissements.append(raison)
        return _enrichir_par_ug(
            projet_id,
            unions,
            buffer_m=FALLBACK_BUFFER_M,
            result=result,
            raison=raison,
        )

    result.methode = "bbox_carroyage" if sous_methode == "bbox_carroyage" else "bbox_projet"
    result.buffer_m = float(BBOX_MARGIN_M)
    result.methode_raison = (
        f"{sous_methode} — numberMatched={matched} "
        f"(cap_tuile={LIMIT_CAP_TUILE}, cap_zone={MAX_PARCELLES_ZONE}), "
        f"{len(features)} parcelles uniques stockées"
    )
    logger.info(
        "[cadastre] méthode=%s RETENUE | numberMatched=%s | parcelles=%s | ugs=%s",
        result.methode,
        matched,
        len(features),
        sorted(unions.keys()),
    )
    supprimer_cadastre_projet(projet_id)
    n = remplacer_parcelles_ug(
        projet_id=projet_id,
        ug_id=UG_ID_PROJET,
        features=features,
        buffer_m=BBOX_MARGIN_M,
    )
    result.ugs_traitees = sorted(unions.keys())
    result.nb_parcelles = n
    result.details.append({
        "ug_id": UG_ID_PROJET,
        "nb_parcelles": n,
        "methode": result.methode,
        "sous_methode": sous_methode,
        "nb_ugs_couvertes": len(unions),
        "number_matched_premier_appel": result.number_matched_premier_appel,
    })
    _croiser_ugs(projet_id, result)
    return result
