"""Persistance de toutes les entités d'un dépôt SIG.

L'ingestion propose un rattachement, elle ne filtre jamais. Chaque entité
lisible est écrite dans ``unites_de_gestion_{surf,lin,pct}`` après
reprojection en EPSG:2154. Le statut est déduit du référentiel verrouillé.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from shapely.geometry.base import BaseGeometry

from api.ocr.domain.ug_ids import normalize_ug_id
from api.ocr.ingest_erc.ingest_erc.modeles import (
    CoucheProfil,
    ReferentielVerrouille,
    UGVerrouillee,
    ZoneCandidate,
)
from api.ocr.ingest_erc.ingest_erc.sig_profil import extraire_zip

from .apercu import lire_gdf
from .ingestion import (
    CoucheKind,
    GeometryIngestError,
    _connect,
    _detect_kind,
    _ensure_multi,
    _json_safe_value,
    _table_for,
)

EPSG_CIBLE = 2154
StatutEntite = Literal["ug", "contexte", "non_affectee", "ecartee"]
TypeGeom = Literal["surf", "lin", "pct"]

_SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".qpj", ".fix")


@dataclass
class Destination:
    statut: StatutEntite
    ug_id: str | None = None
    libelle: str = ""
    motif: str | None = None
    confiance: str | None = None
    zone_id: str | None = None


@dataclass
class PropositionReingestion:
    couche: str
    index_entite: int
    type: TypeGeom
    entite_id: str
    actuel: dict[str, Any]
    proposition: dict[str, Any]


@dataclass
class RapportDepot:
    depot_id: str
    nb_attendues: int
    nb_ecrites: int
    nb_par_statut: dict[str, int] = field(default_factory=dict)
    nb_par_type: dict[str, int] = field(default_factory=dict)
    couches_sans_crs: list[dict[str, Any]] = field(default_factory=list)
    propositions: list[dict[str, Any]] = field(default_factory=list)
    avertissements: list[dict[str, Any]] = field(default_factory=list)
    invariant_ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "depot_id": self.depot_id,
            "nb_attendues": self.nb_attendues,
            "nb_ecrites": self.nb_ecrites,
            "nb_par_statut": self.nb_par_statut,
            "nb_par_type": self.nb_par_type,
            "couches_sans_crs": self.couches_sans_crs,
            "propositions": self.propositions,
            "avertissements": self.avertissements,
            "invariant_ok": self.invariant_ok,
            "bloquant": not self.invariant_ok,
        }


def sha256_fichier(chemin: str | Path) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _zones_normalisees(zones: list[ZoneCandidate] | list[dict]) -> list[ZoneCandidate]:
    out: list[ZoneCandidate] = []
    for z in zones:
        if isinstance(z, dict):
            z = ZoneCandidate(**z)
        out.append(z)
    return out


def _couches_normalisees(couches: list[CoucheProfil] | list[dict] | None) -> list[CoucheProfil]:
    if not couches:
        return []
    out: list[CoucheProfil] = []
    for c in couches:
        if isinstance(c, dict):
            c = CoucheProfil(**c)
        out.append(c)
    return out


def zone_pour_entite(
    couche: str,
    attrs: dict[str, Any],
    zones: list[ZoneCandidate],
) -> ZoneCandidate | None:
    """Zone la plus spécifique (filtre attributaire) qui contient l'entité."""
    matches: list[ZoneCandidate] = []
    for z in zones:
        if z.couche != couche:
            continue
        if z.filtre:
            if all(str(attrs.get(k, "")) == str(v) for k, v in z.filtre.items()):
                matches.append(z)
        else:
            matches.append(z)
    matches.sort(key=lambda z: 0 if z.filtre else 1)
    return matches[0] if matches else None


def _ids_contexte(decisions: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in decisions.get("couches_contexte") or []:
        if isinstance(c, dict):
            zid = str(c.get("zone") or c.get("id") or "")
            if zid:
                out[zid] = str(c.get("motif") or "couche de contexte")
        elif c:
            out[str(c)] = "couche de contexte"
    return out


def _ids_ignorees(decisions: dict) -> set[str]:
    out: set[str] = set()
    for c in decisions.get("couches_ignorees") or []:
        if isinstance(c, dict):
            zid = str(c.get("zone") or c.get("id") or "")
            if zid:
                out.add(zid)
        elif c:
            out.add(str(c))
    return out


def _motif_provenance(prov: dict | None, ug: UGVerrouillee) -> str:
    if not prov:
        return f"zone_sig {ug.zone_sig}"
    if prov.get("origine") == "be":
        return "inférence confirmée par le BE"
    sources = prov.get("sources") or []
    if sources:
        loc = (sources[0] or {}).get("loc") or ""
        if loc:
            return f"attribut {loc}" if ":" not in loc else loc
    return "rapprochement automatique"


def destination_entite(
    zone: ZoneCandidate | None,
    ref: ReferentielVerrouille,
) -> Destination:
    """Statut proposé à partir du référentiel verrouillé — jamais un filtre."""
    if zone is None:
        return Destination(statut="non_affectee", motif="aucune destination")

    decisions = ref.decisions or {}
    contexte = _ids_contexte(decisions)
    ignorees = _ids_ignorees(decisions)

    for u in ref.ugs:
        if u.zone_sig is None:
            continue
        if str(u.zone_sig) == zone.id:
            prov = (ref.provenance or {}).get(f"ug.{u.ug_code}.zone_sig") or {}
            code = normalize_ug_id(str(u.ug_code)) or str(u.ug_code)
            return Destination(
                statut="ug",
                ug_id=code,
                libelle=str(u.libelle or u.ug_code),
                motif=_motif_provenance(prov, u),
                confiance=prov.get("confiance"),
                zone_id=zone.id,
            )

    if zone.id in contexte:
        return Destination(
            statut="contexte",
            motif=contexte[zone.id],
            zone_id=zone.id,
        )
    if zone.couche in contexte:
        return Destination(
            statut="contexte",
            motif=contexte[zone.couche],
            zone_id=zone.id,
        )

    if zone.id in ignorees or zone.couche in ignorees:
        return Destination(
            statut="non_affectee",
            motif="couche ignorée à la validation (pas une UG)",
            zone_id=zone.id,
        )

    return Destination(
        statut="non_affectee",
        motif="aucune destination",
        zone_id=zone.id,
    )


def _attrs_ligne(row: Any, attr_cols: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in attr_cols:
        v = _json_safe_value(row[k])
        if v is not None:
            out[str(k)] = v
    return out


def _zip_sidecars(shp: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in _SIDECARS:
            p = shp.with_suffix(ext)
            if p.exists():
                zf.writestr(p.name, p.read_bytes())
    return buf.getvalue()


def _stocker_couche_sans_crs(
    *,
    projet_id: UUID,
    depot_id: str,
    shp: Path,
    nb_entites: int,
) -> dict[str, Any]:
    document_id = None
    avertissement = None
    try:
        from api.documents.crud_document import upload_document

        row = upload_document(
            projet_id=projet_id,
            file_name=f"{shp.stem}_sans_crs.zip",
            content=_zip_sidecars(shp),
            content_type="application/zip",
            categorie="technique",
            date_document=None,
            description="Couche SIG sans CRS identifiable ; EPSG à saisir pour relancer l'écriture.",
            nom=f"Couche non géoréférencée — {shp.stem}",
            sous_dossier="sig_sans_crs",
        )
        document_id = str(row.get("id")) if isinstance(row, dict) else None
    except Exception as exc:  # noqa: BLE001
        avertissement = f"Bucket documents indisponible pour {shp.name} : {exc}"

    rec: dict[str, Any] = {
        "couche": shp.stem,
        "fichier": shp.name,
        "nb_entites": nb_entites,
        "document_id": document_id,
        "motif": "couche non géoréférencée",
    }
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bancarisation.couche_non_georeferencee
                        (projet_id, depot_id, couche, fichier, document_id,
                         nb_entites, motif)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (projet_id, depot_id, couche) DO UPDATE
                      SET fichier = EXCLUDED.fichier,
                          document_id = COALESCE(EXCLUDED.document_id, couche_non_georeferencee.document_id),
                          nb_entites = EXCLUDED.nb_entites
                    RETURNING id::text
                    """,
                    (
                        str(projet_id), depot_id, shp.stem, shp.name,
                        document_id, nb_entites, "couche non géoréférencée",
                    ),
                )
                rec["id"] = cur.fetchone()[0]
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        rec["erreur"] = str(exc)[:240]
    if avertissement:
        rec["avertissement"] = avertissement
    return rec


def _ligne_protege(
    cur: Any,
    projet_id: str,
    couche: str,
    index_entite: int,
) -> dict[str, Any] | None:
    for kind in ("surf", "lin", "pct"):
        table = _table_for(kind)
        cur.execute(
            f"""
            SELECT id::text, statut, ug_id, origine, modifie_le, depot_id
            FROM bancarisation.{table}
            WHERE projet_id = %s AND couche = %s AND index_entite = %s
              AND (origine = 'user' OR modifie_le IS NOT NULL)
            ORDER BY modifie_le DESC NULLS LAST
            LIMIT 1
            """,
            (projet_id, couche, index_entite),
        )
        row = cur.fetchone()
        if row:
            cols = [d.name for d in cur.description]
            data = dict(zip(cols, row))
            data["type"] = kind
            return data
    return None


def _deja_ecrite(
    cur: Any,
    projet_id: str,
    depot_id: str,
    couche: str,
    index_entite: int,
) -> bool:
    for kind in ("surf", "lin", "pct"):
        table = _table_for(kind)
        cur.execute(
            f"""
            SELECT 1 FROM bancarisation.{table}
            WHERE projet_id = %s AND depot_id = %s
              AND couche = %s AND index_entite = %s
            LIMIT 1
            """,
            (projet_id, depot_id, couche, index_entite),
        )
        if cur.fetchone():
            return True
    return False


def _inserer_entite(
    cur: Any,
    *,
    projet_id: str,
    kind: CoucheKind,
    dest: Destination,
    geom_2154: BaseGeometry,
    properties: dict[str, Any],
    attributs: list[dict[str, Any]],
    source_fichier: str,
    couche: str,
    index_entite: int,
    depot_id: str,
) -> str:
    table = _table_for(kind)
    wkt = geom_2154.wkt
    if kind == "emprise":
        cur.execute(
            f"""
            INSERT INTO bancarisation.{table}
                (projet_id, libelle, description, geom, geom_3857,
                 properties, attributs, source_fichier)
            VALUES (
                %s, %s, %s,
                ST_MakeValid(ST_SetSRID(ST_GeomFromText(%s), {EPSG_CIBLE})),
                ST_Transform(
                    ST_MakeValid(ST_SetSRID(ST_GeomFromText(%s), {EPSG_CIBLE})), 3857),
                %s::jsonb, %s::jsonb, %s
            )
            RETURNING id::text
            """,
            (
                projet_id, dest.libelle or couche, dest.motif or "",
                wkt, wkt,
                json.dumps(properties, default=str),
                json.dumps(attributs, default=str),
                source_fichier,
            ),
        )
        return cur.fetchone()[0]

    cur.execute(
        f"""
        INSERT INTO bancarisation.{table}
            (projet_id, ug_id, libelle, description, geom, geom_3857,
             properties, attributs, source_fichier,
             statut, couche, index_entite, depot_id, zone_id,
             motif, origine, confiance)
        VALUES (
            %s, %s, %s, %s,
            ST_MakeValid(ST_SetSRID(ST_GeomFromText(%s), {EPSG_CIBLE})),
            ST_Transform(
                ST_MakeValid(ST_SetSRID(ST_GeomFromText(%s), {EPSG_CIBLE})), 3857),
            %s::jsonb, %s::jsonb, %s,
            %s, %s, %s, %s, %s,
            %s, 'ia', %s
        )
        RETURNING id::text
        """,
        (
            projet_id, dest.ug_id, dest.libelle or "", dest.motif or "",
            wkt, wkt,
            json.dumps(properties, default=str),
            json.dumps(attributs, default=str),
            source_fichier,
            dest.statut, couche, index_entite, depot_id, dest.zone_id,
            dest.motif, dest.confiance,
        ),
    )
    return cur.fetchone()[0]


def _compter_depot(cur: Any, projet_id: str, depot_id: str) -> int:
    total = 0
    for kind in ("surf", "lin", "pct"):
        table = _table_for(kind)
        cur.execute(
            f"""
            SELECT count(*) FROM bancarisation.{table}
            WHERE projet_id = %s AND depot_id = %s
            """,
            (projet_id, depot_id),
        )
        total += int(cur.fetchone()[0])
    return total


def persister_depot_sig(
    *,
    projet_id: UUID | str,
    ref: ReferentielVerrouille,
    sig_zip: str | Path,
    zones: list[ZoneCandidate] | list[dict],
    couches: list[CoucheProfil] | list[dict] | None = None,
    depot_id: str | None = None,
) -> dict[str, Any]:
    """Écrit toutes les entités lisibles du ZIP. Ne filtre jamais."""
    pid = str(projet_id)
    zip_path = Path(sig_zip)
    if not zip_path.is_file():
        raise GeometryIngestError(f"Archive SIG introuvable : {zip_path}")

    depot = depot_id or sha256_fichier(zip_path)
    zones_n = _zones_normalisees(zones)
    couches_n = _couches_normalisees(couches)
    par_couche = {c.nom: c for c in couches_n}

    dossier = extraire_zip(str(zip_path))
    shps = sorted(p for p in dossier.glob("*.shp") if not p.name.startswith("._"))

    rapport = RapportDepot(depot_id=depot, nb_attendues=0, nb_ecrites=0)
    source_zip = zip_path.name

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                for shp in shps:
                    try:
                        gdf = lire_gdf(shp)
                    except Exception as exc:  # noqa: BLE001
                        rapport.avertissements.append({
                            "niveau": "erreur",
                            "couche": shp.stem,
                            "message": f"Lecture impossible : {exc}",
                        })
                        continue

                    try:
                        epsg = int(gdf.crs.to_epsg()) if gdf.crs is not None else None
                    except Exception:  # noqa: BLE001
                        epsg = None

                    nb = int(len(gdf))
                    profil = par_couche.get(shp.stem)
                    if profil and profil.nb_entites:
                        nb = int(profil.nb_entites)

                    if not epsg:
                        rec = _stocker_couche_sans_crs(
                            projet_id=UUID(pid),
                            depot_id=depot,
                            shp=shp,
                            nb_entites=nb,
                        )
                        rapport.couches_sans_crs.append(rec)
                        continue

                    rapport.nb_attendues += nb

                    try:
                        gdf_2154 = gdf.to_crs(epsg=EPSG_CIBLE) if epsg != EPSG_CIBLE else gdf
                    except Exception as exc:  # noqa: BLE001
                        rapport.avertissements.append({
                            "niveau": "erreur",
                            "couche": shp.stem,
                            "message": f"Reprojection 2154 impossible : {exc}",
                        })
                        continue

                    attr_cols = [c for c in gdf_2154.columns if c != "geometry"]

                    for i, (_, row) in enumerate(gdf_2154.iterrows()):
                        geom = row.geometry
                        attrs = _attrs_ligne(row, attr_cols)
                        zone = zone_pour_entite(shp.stem, attrs, zones_n)
                        dest = destination_entite(zone, ref)

                        if geom is None or geom.is_empty:
                            rapport.avertissements.append({
                                "niveau": "ecart",
                                "couche": shp.stem,
                                "index_entite": i,
                                "message": "géométrie vide — non écrite",
                            })
                            continue

                        try:
                            kind = _detect_kind(geom, is_emprise=False)
                            multi = _ensure_multi(geom, kind)
                        except GeometryIngestError as exc:
                            rapport.avertissements.append({
                                "niveau": "ecart",
                                "couche": shp.stem,
                                "index_entite": i,
                                "message": str(exc),
                            })
                            continue

                        if _deja_ecrite(cur, pid, depot, shp.stem, i):
                            continue

                        protege = _ligne_protege(cur, pid, shp.stem, i)
                        if protege:
                            rapport.propositions.append({
                                "couche": shp.stem,
                                "index_entite": i,
                                "type": protege["type"],
                                "entite_id": protege["id"],
                                "actuel": {
                                    "statut": protege.get("statut"),
                                    "ug_id": protege.get("ug_id"),
                                    "origine": protege.get("origine"),
                                    "modifie_le": str(protege.get("modifie_le") or ""),
                                    "depot_id": protege.get("depot_id"),
                                },
                                "proposition": {
                                    "statut": dest.statut,
                                    "ug_id": dest.ug_id,
                                    "libelle": dest.libelle,
                                    "motif": dest.motif,
                                    "confiance": dest.confiance,
                                    "zone_id": dest.zone_id,
                                },
                            })
                            continue

                        try:
                            _inserer_entite(
                                cur,
                                projet_id=pid,
                                kind=kind,
                                dest=dest,
                                geom_2154=multi,
                                properties=attrs,
                                attributs=[attrs] if attrs else [],
                                source_fichier=source_zip,
                                couche=shp.stem,
                                index_entite=i,
                                depot_id=depot,
                            )
                        except Exception as exc:  # noqa: BLE001
                            rapport.avertissements.append({
                                "niveau": "ecart",
                                "couche": shp.stem,
                                "index_entite": i,
                                "message": f"écriture refusée : {exc}",
                            })
                            continue

                        rapport.nb_par_statut[dest.statut] = (
                            rapport.nb_par_statut.get(dest.statut, 0) + 1
                        )
                        rapport.nb_par_type[kind] = rapport.nb_par_type.get(kind, 0) + 1

                conn.commit()
                rapport.nb_ecrites = _compter_depot(cur, pid, depot)
    except GeometryIngestError:
        raise
    except Exception as exc:
        raise GeometryIngestError(f"Persistance du dépôt SIG impossible : {exc}") from exc

    if rapport.nb_ecrites != rapport.nb_attendues:
        rapport.invariant_ok = False
        rapport.avertissements.append({
            "niveau": "bloquant",
            "message": (
                f"Invariant d'ingestion : {rapport.nb_ecrites} lignes écrites "
                f"pour le dépôt {depot[:12]}…, {rapport.nb_attendues} entités "
                f"attendues (somme des couches lisibles)."
            ),
            "nb_ecrites": rapport.nb_ecrites,
            "nb_attendues": rapport.nb_attendues,
        })

    return rapport.to_dict()
