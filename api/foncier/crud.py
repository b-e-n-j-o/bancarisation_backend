"""Lecture GeoJSON et import cadastre → parcelles foncières."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg

from api.cadastre.enrichissement import enrichir_cadastre_projet
from api.cadastre.ign_client import CadastreIgnError
from api.cadastre.persist import lier_parcelles_aux_ugs
from api.db.env import get_database_url

NATURES_PROPRIETE = ("propriete", "usufruit", "nue_propriete")


class FoncierError(Exception):
    pass


def _connect():
    return psycopg.connect(get_database_url())


def geojson_parcelles(projet_id: UUID) -> dict[str, Any]:
    """FeatureCollection 4326 des parcelles du projet qui ont une géométrie."""
    features: list[dict[str, Any]] = []
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      p.id::text,
                      p.idu,
                      p.commune,
                      p.section,
                      p.numero,
                      ST_AsGeoJSON(p.geom_4326)::text,
                      d.id::text,
                      d.nature,
                      d.statut,
                      d.date_debut::text,
                      d.date_fin::text,
                      pe.id::text,
                      pe.nom,
                      pe.prenom,
                      pe.type_personne
                    FROM bancarisation.parcelles p
                    LEFT JOIN bancarisation.droits_fonciers d
                      ON d.parcelle_id = p.id
                    LEFT JOIN bancarisation.personnes pe
                      ON pe.id = d.titulaire_id
                    WHERE p.projet_id = %s
                      AND p.geom_4326 IS NOT NULL
                    ORDER BY p.section NULLS LAST, p.numero NULLS LAST, p.idu, d.date_debut
                    """,
                    (str(projet_id),),
                )
                by_id: dict[str, dict[str, Any]] = {}
                for row in cur.fetchall():
                    (
                        pid,
                        idu,
                        commune,
                        section,
                        numero,
                        geom_json,
                        droit_id,
                        nature,
                        statut,
                        date_debut,
                        date_fin,
                        titulaire_id,
                        titulaire_nom,
                        titulaire_prenom,
                        type_personne,
                    ) = row
                    if not geom_json:
                        continue
                    feat = by_id.get(pid)
                    if feat is None:
                        feat = {
                            "type": "Feature",
                            "id": pid,
                            "geometry": json.loads(geom_json),
                            "properties": {
                                "id": pid,
                                "idu": idu,
                                "commune": commune,
                                "section": section,
                                "numero": numero,
                                "titulaire_id": None,
                                "titulaire_nom": None,
                                "ugs": [],
                                "titres": [],
                            },
                        }
                        by_id[pid] = feat
                    if droit_id:
                        prenom = (titulaire_prenom or "").strip()
                        nom_affiche = titulaire_nom
                        if titulaire_nom and prenom and type_personne != "morale":
                            nom_affiche = f"{titulaire_nom} {prenom}"
                        feat["properties"]["titres"].append(
                            {
                                "nature": nature,
                                "statut": statut,
                                "date_debut": date_debut,
                                "date_fin": date_fin,
                                "titulaire_id": titulaire_id,
                                "titulaire_nom": nom_affiche,
                            }
                        )
                        if (
                            nature in NATURES_PROPRIETE
                            and titulaire_id
                            and feat["properties"]["titulaire_id"] is None
                        ):
                            feat["properties"]["titulaire_id"] = titulaire_id
                            feat["properties"]["titulaire_nom"] = nom_affiche
                for feat in by_id.values():
                    props = feat["properties"]
                    if props["titulaire_id"] is None:
                        for titre in props["titres"]:
                            if titre.get("titulaire_id"):
                                props["titulaire_id"] = titre["titulaire_id"]
                                props["titulaire_nom"] = titre.get("titulaire_nom")
                                break
                    features.append(feat)

                cur.execute(
                    """
                    SELECT p.id::text, COALESCE(NULLIF(u.libelle, ''), u.ug_id)
                    FROM bancarisation.parcelles p
                    JOIN bancarisation.ug_surf_parcelles l ON l.parcelle_id = p.id
                    JOIN bancarisation.unites_de_gestion_surf u ON u.id = l.ug_id
                    WHERE p.projet_id = %s
                    UNION
                    SELECT p.id::text, COALESCE(NULLIF(u.libelle, ''), u.ug_id)
                    FROM bancarisation.parcelles p
                    JOIN bancarisation.ug_lin_parcelles l ON l.parcelle_id = p.id
                    JOIN bancarisation.unites_de_gestion_lin u ON u.id = l.ug_id
                    WHERE p.projet_id = %s
                    UNION
                    SELECT p.id::text, COALESCE(NULLIF(u.libelle, ''), u.ug_id)
                    FROM bancarisation.parcelles p
                    JOIN bancarisation.ug_pct_parcelles l ON l.parcelle_id = p.id
                    JOIN bancarisation.unites_de_gestion_pct u ON u.id = l.ug_id
                    WHERE p.projet_id = %s
                    """,
                    (str(projet_id), str(projet_id), str(projet_id)),
                )
                for pid, label in cur.fetchall():
                    feat = by_id.get(pid)
                    if not feat:
                        continue
                    ugs = feat["properties"].setdefault("ugs", [])
                    if label and label not in ugs:
                        ugs.append(label)
    except Exception as exc:
        raise FoncierError(f"Lecture geojson foncier impossible: {exc}") from exc

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {"nb_parcelles": len(features)},
    }


def _insert_depuis_cadastre_sql(*, seulement_croisees: bool) -> str:
    """Copie cadastre IGN → parcelles foncières.

    ``seulement_croisees`` : uniquement les IDU qui intersectent une UG
    (table ``cadastre_parcelle_ug``). Sinon un IDU précis (clic carte).
    """
    filtre_idu = "AND (%s::text IS NULL OR c.idu = %s)"
    filtre_croisement = """
        AND EXISTS (
          SELECT 1 FROM bancarisation.cadastre_parcelle_ug link
          WHERE link.projet_id = c.projet_id AND link.idu = c.idu
        )
    """ if seulement_croisees else ""
    return f"""
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
        {filtre_idu}
        {filtre_croisement}
        RETURNING id::text, idu
    """


def _lier_ugs_sql(table: str) -> str:
    extra_cols = ""
    extra_vals = ""
    if table == "ug_surf_parcelles":
        extra_cols = ", emprise_partielle, surface_incluse_m2"
        extra_vals = """,
          CASE
            WHEN link.surface_inter_m2 IS NOT NULL AND p.contenance_m2 IS NOT NULL
            THEN link.surface_inter_m2 < p.contenance_m2 * 0.98
            ELSE false
          END,
          link.surface_inter_m2"""
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
        SELECT DISTINCT u.id, p.id{extra_vals}
        FROM bancarisation.cadastre_parcelle_ug link
        JOIN bancarisation.parcelles p
          ON p.projet_id = link.projet_id AND p.idu = link.idu
        JOIN bancarisation.{ug_table} u
          ON u.projet_id = link.projet_id AND u.ug_id = link.ug_id
        WHERE link.projet_id = %s
          AND (%s::text IS NULL OR link.idu = %s)
        ON CONFLICT (ug_id, parcelle_id) DO NOTHING
    """


def importer_depuis_cadastre(
    projet_id: UUID,
    *,
    idu: str | None = None,
) -> dict[str, Any]:
    """Alimente ``parcelles`` avec le croisement UG ∩ cadastre IGN.

    Sans ``idu`` : uniquement les parcelles qui intersectent une UG.
    Avec ``idu`` : cette parcelle (clic carte), même hors croisement.
    Remplit aussi ``ug_*_parcelles``.
    """
    pid = str(projet_id)
    enrichissement: dict[str, Any] | None = None
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM bancarisation.projets WHERE id = %s", (pid,))
                if cur.fetchone() is None:
                    raise FoncierError("Projet introuvable.")
                cur.execute(
                    """
                    SELECT COUNT(*) FROM bancarisation.cadastre_parcelle
                    WHERE projet_id = %s
                    """,
                    (pid,),
                )
                nb_cadastre = int(cur.fetchone()[0])
                cur.execute(
                    """
                    SELECT EXISTS (
                      SELECT 1 FROM bancarisation.unites_de_gestion_surf
                      WHERE projet_id = %s AND geom_3857 IS NOT NULL
                      UNION ALL
                      SELECT 1 FROM bancarisation.unites_de_gestion_lin
                      WHERE projet_id = %s AND geom_3857 IS NOT NULL
                      UNION ALL
                      SELECT 1 FROM bancarisation.unites_de_gestion_pct
                      WHERE projet_id = %s AND geom_3857 IS NOT NULL
                    )
                    """,
                    (pid, pid, pid),
                )
                has_ug = bool(cur.fetchone()[0])

        if nb_cadastre == 0:
            if not has_ug:
                raise FoncierError(
                    "Aucune UG géométrique sur ce projet. Importez les shapefiles "
                    "depuis l’onglet Dossier / Carto."
                )
            try:
                result = enrichir_cadastre_projet(projet_id)
            except CadastreIgnError as exc:
                raise FoncierError(f"Cadastre IGN inaccessible : {exc}") from exc
            except Exception as exc:  # noqa: BLE001
                raise FoncierError(f"Croisement cadastre IGN impossible : {exc}") from exc
            enrichissement = result.to_dict()
            nb_cadastre = int(result.nb_parcelles or 0)
            if nb_cadastre == 0:
                avert = result.avertissements[0] if result.avertissements else (
                    "Le cadastre IGN n’a renvoyé aucune parcelle autour des UG."
                )
                raise FoncierError(avert)

        if idu is None:
            lier_parcelles_aux_ugs(projet_id)

        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT idu)
                    FROM bancarisation.cadastre_parcelle_ug
                    WHERE projet_id = %s
                    """,
                    (pid,),
                )
                nb_croisees = int(cur.fetchone()[0])
                cur.execute(
                    _insert_depuis_cadastre_sql(seulement_croisees=idu is None),
                    (pid, idu, idu),
                )
                inserted = [{"id": row[0], "idu": row[1]} for row in cur.fetchall()]
                if idu is None:
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
                    cur.execute(_lier_ugs_sql(table), (pid, idu, idu))
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
    except FoncierError:
        raise
    except Exception as exc:
        detail = str(exc)
        if "cadastre_parcelle" in detail.lower() and (
            "does not exist" in detail.lower() or "undefinedtable" in detail.lower().replace(" ", "")
        ):
            raise FoncierError(
                "Table cadastre absente — enrichir le cadastre depuis l’onglet Carto."
            ) from exc
        raise FoncierError(f"Import cadastre impossible: {exc}") from exc

    return {
        "nb_importees": len(inserted),
        "nb_croisees": nb_croisees,
        "nb_liens_ug": nb_liens,
        "parcelles": inserted,
        "idu": idu,
        "enrichissement": enrichissement,
    }
