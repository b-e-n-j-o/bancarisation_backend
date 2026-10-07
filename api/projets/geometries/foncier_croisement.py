"""Croisement UG ∩ cadastre IGN à l'ingestion des géométries projet.

Après écriture des UG (base ou dépôt en cours), interroge le cadastre IGN
(API Carto / WFS PCI) autour des géométries, garde uniquement les parcelles
qui intersectent une UG, et les copie dans ``bancarisation.parcelles``.
L'onglet Foncier ne fait plus qu'afficher ce jeu persisté.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import psycopg

from api.cadastre.enrichissement import enrichir_cadastre_projet
from api.cadastre.ign_client import CadastreIgnError
from api.cadastre.persist import lier_parcelles_aux_ugs
from api.db.utilisateur import connect_utilisateur

logger = logging.getLogger(__name__)


class FoncierCroisementError(Exception):
    pass


def _connect():
    return connect_utilisateur()


def _insert_parcelles_croisees_sql() -> str:
    return """
        INSERT INTO bancarisation.parcelles (
          organisation_id, projet_id, idu, code_insee, commune, prefixe,
          section, numero, contenance_m2, millesime_cadastre, geom, source
        )
        SELECT
          pr.organisation_id,
          c.projet_id,
          c.idu,
          c.code_insee,
          c.nom_com,
          CASE WHEN length(c.idu) = 14 THEN substring(c.idu from 6 for 3) ELSE NULL END,
          c.section,
          c.numero,
          c.contenance,
          NULL,
          ST_Multi(
            ST_Transform(
              COALESCE(c.geom, ST_Transform(c.geom_3857, 4326)),
              2154
            )
          ),
          'cadastre_ign'
        FROM (
          SELECT DISTINCT ON (idu)
            projet_id, idu, code_insee, nom_com, section, numero,
            contenance, geom, geom_3857
          FROM bancarisation.cadastre_parcelle
          WHERE projet_id = %s
          ORDER BY idu, fetched_at DESC
        ) c
        JOIN bancarisation.projets pr ON pr.id = c.projet_id
        WHERE NOT EXISTS (
          SELECT 1
          FROM bancarisation.parcelles x
          WHERE x.projet_id = c.projet_id
            AND x.idu IS NOT DISTINCT FROM c.idu
        )
        AND EXISTS (
          SELECT 1 FROM bancarisation.cadastre_parcelle_ug link
          WHERE link.projet_id = c.projet_id AND link.idu = c.idu
        )
        RETURNING id::text, idu
    """


def _assurer_schema_croisement(cur) -> None:
    cur.execute(
        """
        ALTER TABLE bancarisation.ug_surf_parcelles
          ADD COLUMN IF NOT EXISTS part_ug_pct numeric
        """
    )


def _aire_parcelle_sql() -> str:
    """Superficie de la parcelle (geom calculée, sinon contenance cadastrale)."""
    return """COALESCE(
          NULLIF(p.surface_calculee_m2, 0),
          NULLIF(p.contenance_m2, 0),
          ST_Area(p.geom)
        )"""


def _lier_ugs_sql(table: str) -> str:
    extra_cols = ""
    extra_vals = ""
    conflict = "DO NOTHING"
    if table == "ug_surf_parcelles":
        aire = _aire_parcelle_sql()
        extra_cols = ", emprise_partielle, surface_incluse_m2, part_ug_pct"
        extra_vals = f""",
          CASE
            WHEN link.surface_inter_m2 IS NOT NULL AND p.contenance_m2 IS NOT NULL
            THEN link.surface_inter_m2 < p.contenance_m2 * 0.98
            ELSE false
          END,
          link.surface_inter_m2,
          CASE
            WHEN link.surface_inter_m2 IS NULL THEN NULL
            WHEN COALESCE({aire}, 0) <= 0 THEN NULL
            ELSE ROUND((100.0 * link.surface_inter_m2 / {aire})::numeric, 2)
          END"""
        conflict = """DO UPDATE SET
          emprise_partielle = EXCLUDED.emprise_partielle,
          surface_incluse_m2 = EXCLUDED.surface_incluse_m2,
          part_ug_pct = EXCLUDED.part_ug_pct"""
    elif table == "ug_lin_parcelles":
        extra_cols = ", longueur_incluse_m"
        extra_vals = ", NULL::numeric"
    ug_table = {
        "ug_surf_parcelles": "unites_de_gestion_surf",
        "ug_lin_parcelles": "unites_de_gestion_lin",
        "ug_pct_parcelles": "unites_de_gestion_pct",
    }[table]
    return f"""
        INSERT INTO bancarisation.{table} (ug_id, parcelle_id{extra_cols})
        SELECT DISTINCT ON (p.id, link.ug_id)
          u.id, p.id{extra_vals}
        FROM bancarisation.cadastre_parcelle_ug link
        JOIN bancarisation.parcelles p
          ON p.projet_id = link.projet_id AND p.idu = link.idu
        JOIN bancarisation.{ug_table} u
          ON u.projet_id = link.projet_id AND u.ug_id = link.ug_id
         AND u.statut = 'ug'
        WHERE link.projet_id = %s
        ORDER BY p.id, link.ug_id, u.id
        ON CONFLICT (ug_id, parcelle_id) {conflict}
    """


def _dedupe_liens_ug_sql() -> str:
    """Un seul lien par parcelle et code UG (une UG a souvent plusieurs entités)."""
    return """
        DELETE FROM bancarisation.ug_surf_parcelles l
        USING bancarisation.unites_de_gestion_surf u
        WHERE l.ug_id = u.id AND u.projet_id = %s
          AND l.id <> (
            SELECT l2.id
            FROM bancarisation.ug_surf_parcelles l2
            JOIN bancarisation.unites_de_gestion_surf u2 ON u2.id = l2.ug_id
            WHERE l2.parcelle_id = l.parcelle_id AND u2.ug_id = u.ug_id
            ORDER BY l2.surface_incluse_m2 DESC NULLS LAST, l2.id
            LIMIT 1
          );
        DELETE FROM bancarisation.ug_lin_parcelles l
        USING bancarisation.unites_de_gestion_lin u
        WHERE l.ug_id = u.id AND u.projet_id = %s
          AND l.id <> (
            SELECT l2.id
            FROM bancarisation.ug_lin_parcelles l2
            JOIN bancarisation.unites_de_gestion_lin u2 ON u2.id = l2.ug_id
            WHERE l2.parcelle_id = l.parcelle_id AND u2.ug_id = u.ug_id
            ORDER BY l2.id
            LIMIT 1
          );
        DELETE FROM bancarisation.ug_pct_parcelles l
        USING bancarisation.unites_de_gestion_pct u
        WHERE l.ug_id = u.id AND u.projet_id = %s
          AND l.id <> (
            SELECT l2.id
            FROM bancarisation.ug_pct_parcelles l2
            JOIN bancarisation.unites_de_gestion_pct u2 ON u2.id = l2.ug_id
            WHERE l2.parcelle_id = l.parcelle_id AND u2.ug_id = u.ug_id
            ORDER BY l2.id
            LIMIT 1
          );
    """


def persister_parcelles_concernees(projet_id: UUID) -> dict[str, Any]:
    """Copie le croisement déjà calculé (UG ∩ IGN) vers ``parcelles``.

    Pas d'appel IGN : lit ``cadastre_parcelle`` / ``cadastre_parcelle_ug``.
    """
    pid = str(projet_id)
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                _assurer_schema_croisement(cur)
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT idu)
                    FROM bancarisation.cadastre_parcelle_ug
                    WHERE projet_id = %s
                    """,
                    (pid,),
                )
                nb_croisees = int(cur.fetchone()[0])
                cur.execute(_insert_parcelles_croisees_sql(), (pid,))
                inserted = [{"id": row[0], "idu": row[1]} for row in cur.fetchall()]
                cur.execute(
                    """
                    DELETE FROM bancarisation.parcelles p
                    WHERE p.projet_id = %s
                      AND COALESCE(p.source, '') = 'cadastre_ign'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM bancarisation.cadastre_parcelle_ug l
                        WHERE l.projet_id = p.projet_id
                          AND l.idu IS NOT DISTINCT FROM p.idu
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM bancarisation.droits_fonciers d
                        WHERE d.parcelle_id = p.id
                      )
                    """,
                    (pid,),
                )
                for table in (
                    "ug_surf_parcelles",
                    "ug_lin_parcelles",
                    "ug_pct_parcelles",
                ):
                    cur.execute(_lier_ugs_sql(table), (pid,))
                cur.execute(_dedupe_liens_ug_sql(), (pid, pid, pid))
                nb_liens = 0
                for table in (
                    "ug_surf_parcelles",
                    "ug_lin_parcelles",
                    "ug_pct_parcelles",
                ):
                    cur.execute(
                        f"""
                        SELECT COUNT(*) FROM bancarisation.{table} l
                        JOIN bancarisation.parcelles p ON p.id = l.parcelle_id
                        WHERE p.projet_id = %s
                        """,
                        (pid,),
                    )
                    nb_liens += int(cur.fetchone()[0])
            conn.commit()
    except Exception as exc:
        detail = str(exc)
        low = detail.lower()
        if "parcelles" in low and (
            "does not exist" in low or "undefinedtable" in low.replace(" ", "")
        ):
            raise FoncierCroisementError(
                "Table parcelles absente — exécuter le SQL d'init foncier."
            ) from exc
        if "cadastre_parcelle" in low and (
            "does not exist" in low or "undefinedtable" in low.replace(" ", "")
        ):
            raise FoncierCroisementError(
                "Table cadastre absente — le snapshot IGN n'a pas été écrit."
            ) from exc
        raise FoncierCroisementError(f"Persistance des parcelles foncières impossible: {exc}") from exc

    logger.info(
        "[foncier] projet=%s — persisté | croisees=%s | importees=%s | liens_ug=%s",
        projet_id,
        nb_croisees,
        len(inserted),
        nb_liens,
    )
    return {
        "nb_importees": len(inserted),
        "nb_croisees": nb_croisees,
        "nb_liens_ug": nb_liens,
        "parcelles": inserted,
    }


def initialiser_foncier_depuis_ugs(
    projet_id: UUID,
    *,
    rafraichir_ign: bool = True,
) -> dict[str, Any]:
    """Initialise le jeu foncier du projet à partir des UG en base.

    1. Snapshot IGN autour des UG (sauf si ``rafraichir_ign=False``).
    2. Croisement spatial UG ∩ parcelle.
    3. Copie des parcelles concernées dans ``parcelles``.
    """
    enrichissement: dict[str, Any] | None = None
    if rafraichir_ign:
        try:
            result = enrichir_cadastre_projet(projet_id)
        except CadastreIgnError as exc:
            raise FoncierCroisementError(f"Cadastre IGN inaccessible : {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise FoncierCroisementError(
                f"Croisement cadastre IGN impossible : {exc}"
            ) from exc
        enrichissement = result.to_dict()
        if result.nb_parcelles == 0 and result.avertissements:
            logger.warning(
                "[foncier] projet=%s — snapshot IGN vide | %s",
                projet_id,
                result.avertissements[0],
            )
    else:
        try:
            lier_parcelles_aux_ugs(projet_id)
        except Exception as exc:  # noqa: BLE001
            raise FoncierCroisementError(
                f"Recalcul du croisement UG ∩ cadastre impossible : {exc}"
            ) from exc

    persist = persister_parcelles_concernees(projet_id)
    return {
        **persist,
        "enrichissement": enrichissement,
    }
