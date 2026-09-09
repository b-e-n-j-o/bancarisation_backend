"""Persistance PostGIS des plans CAO (aperçu local, non calé)."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.errors import InvalidParameterValue, UndefinedColumn, UndefinedFunction, UndefinedTable
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.db.env import get_database_url

_MSG_TABLES = (
    "Tables plan_cao absentes — appliquer backend/api/ocr/db/sql/035_plan_cao.sql"
    " puis 037_plan_cao_contexte.sql, 038_plan_cao_geom_z.sql et 040_plan_cao_srid_calque.sql"
)
_MSG_GEOM_Z = (
    "geom_local refuse le Z — appliquer backend/api/ocr/db/sql/038_plan_cao_geom_z.sql"
)
_MSG_CRS = (
    "Colonnes CRS absentes — appliquer backend/api/ocr/db/sql/040_plan_cao_srid_calque.sql"
)

logger = logging.getLogger(__name__)

_HABILLAGE = re.compile(
    r"legend|legende|cotation|cartouche|compo_legende|alti_legende|photos|vue_aerienne|_tx-|texte",
    re.I,
)
_ORDER = ("terrain", "projet", "composition", "ecologie", "habillage", "autres")
_LIBELLES = {
    "terrain": "Terrain",
    "projet": "Projet",
    "composition": "Composition",
    "ecologie": "Écologie",
    "habillage": "Habillage",
    "autres": "Autres",
}


class PlanCaoError(Exception):
    pass


def _connect():
    try:
        return psycopg.connect(get_database_url(), row_factory=dict_row, connect_timeout=8)
    except Exception as exc:
        raise PlanCaoError(f"Postgres injoignable : {exc}") from exc


def _calque_api(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "nom": r["nom"],
        "nb": r.get("nb_entites") if r.get("nb_entites") is not None else r.get("nb") or 0,
        "types": r["types_dxf"] if isinstance(r.get("types_dxf"), dict) else (r.get("types") or {}),
        "visible_defaut": r["visible"] if "visible" in r else bool(r.get("visible_defaut", True)),
        "couleur": r.get("couleur"),
        "aci": r.get("aci"),
        "eteint": bool(r.get("eteint")),
        "gele": bool(r.get("gele")),
        "verrouille": bool(r.get("verrouille")),
        "porte_altimetrie": bool(r.get("porte_altimetrie")),
        "z_min": r.get("z_min"),
        "z_max": r.get("z_max"),
        "probable_courbes_niveau": bool(r.get("probable_courbes_niveau")),
        "srid_source": r.get("srid_source"),
        "srid_origine": r.get("srid_origine") or "herite",
        "srid_confiance": r.get("srid_confiance"),
        "cluster_id": r.get("cluster_id"),
        "srid_ambigu": bool(r.get("srid_ambigu")),
        "crs_nom": r.get("crs_nom"),
    }


def _fusionner_groupes(existants: dict[str, Any], calques: list[dict[str, Any]]) -> dict[str, Any]:
    """Garde les groupes utilisateur, rattache les nouveaux calques à une famille suggérée."""
    connus = {c["nom"] for c in calques}
    appartenance = {
        nom: gid
        for nom, gid in (existants.get("appartenance") or {}).items()
        if nom in connus and gid
    }
    groupes = [dict(g) for g in (existants.get("groupes") or [])]
    ids = {g["id"] for g in groupes}
    for c in calques:
        if appartenance.get(c["nom"]):
            continue
        fam = _famille(c["nom"])
        if fam not in ids:
            groupes.append({"id": fam, "nom": _LIBELLES.get(fam, fam)})
            ids.add(fam)
        appartenance[c["nom"]] = fam
    ids_utilises = set(appartenance.values())
    groupes = [g for g in groupes if g["id"] in ids_utilises]
    if not groupes:
        return groupes_suggerees(calques)
    return {"groupes": groupes, "appartenance": appartenance}


def _lire_rangement(
    conn: psycopg.Connection, plan_id: str
) -> tuple[dict[str, Any] | None, set[str] | None, set[str]]:
    groupes_rows = conn.execute(
        """
        SELECT id, nom, ordre FROM bancarisation.plan_cao_groupe
        WHERE plan_id = %s ORDER BY ordre, nom
        """,
        (plan_id,),
    ).fetchall()
    calques_rows = conn.execute(
        """
        SELECT nom, groupe_id, visible FROM bancarisation.plan_cao_calque
        WHERE plan_id = %s
        """,
        (plan_id,),
    ).fetchall()
    noms = {r["nom"] for r in calques_rows}
    if not groupes_rows:
        return None, None, noms
    groupes = {
        "groupes": [{"id": r["id"], "nom": r["nom"]} for r in groupes_rows],
        "appartenance": {
            r["nom"]: r["groupe_id"]
            for r in calques_rows
            if r["groupe_id"]
        },
    }
    visibles = {r["nom"] for r in calques_rows if r["visible"]}
    return groupes, visibles, noms


def _famille(nom: str) -> str:
    n = unicodedata.normalize("NFD", nom)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn").upper()
    if _HABILLAGE.search(n) or "LEGENDE" in n or "COTATION" in n:
        return "habillage"
    if n.startswith("SIMETHIS") or "ARBRES" in n or n == "EBC":
        return "ecologie"
    if n.startswith("T-") or n.startswith("T_"):
        return "terrain"
    if n.startswith("P-") or n.startswith("P_"):
        return "projet"
    if n.startswith("C-") or n.startswith("C_"):
        return "composition"
    return "autres"


def groupes_suggerees(calques: list[dict[str, Any]]) -> dict[str, Any]:
    appartenance: dict[str, str] = {}
    present: set[str] = set()
    for c in calques:
        fam = _famille(c["nom"])
        appartenance[c["nom"]] = fam
        present.add(fam)
    groupes = [
        {"id": fid, "nom": _LIBELLES[fid]}
        for fid in _ORDER
        if fid in present
    ]
    if not groupes:
        groupes = [{"id": "autres", "nom": "Autres"}]
    return {"groupes": groupes, "appartenance": appartenance}


def _bbox_wkt(bbox: dict[str, float] | None) -> str | None:
    if not bbox:
        return None
    xmin, ymin, xmax, ymax = bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"]
    if xmax < xmin:
        xmin, xmax = xmax, xmin
    if ymax < ymin:
        ymin, ymax = ymax, ymin
    if xmax == xmin:
        xmax = xmin + 1e-6
    if ymax == ymin:
        ymax = ymin + 1e-6
    return (
        f"POLYGON(({xmin} {ymin},{xmax} {ymin},{xmax} {ymax},{xmin} {ymax},{xmin} {ymin}))"
    )


def _vider_plan(conn: psycopg.Connection, plan_id: str) -> None:
    """Efface entités (035+037), calques, groupes, puis le plan.

    CASCADE existe déjà depuis `plan_cao`, mais l'ordre explicite évite
    le SET NULL `plan_cao_calque.groupe_id` → `plan_cao_groupe` et reste
    correct si une FK est ajoutée sans cascade.
    """
    conn.execute("DELETE FROM bancarisation.plan_cao_entite WHERE plan_id = %s", (plan_id,))
    conn.execute("DELETE FROM bancarisation.plan_cao_calque WHERE plan_id = %s", (plan_id,))
    conn.execute("DELETE FROM bancarisation.plan_cao_groupe WHERE plan_id = %s", (plan_id,))
    conn.execute("DELETE FROM bancarisation.plan_cao WHERE id = %s", (plan_id,))


def _ids_documents_cao(
    conn: psycopg.Connection,
    projet_id: UUID,
    nom_fichier: str,
    document_id: str | None,
) -> list[str]:
    """document_id du plan + orphelins d'un réimport (même projet, catégorie cao)."""
    ids: list[str] = []
    if document_id:
        ids.append(document_id)
    try:
        with conn.transaction():
            extras = conn.execute(
                """
                SELECT id::text FROM bancarisation.documents
                WHERE projet_id = %s AND categorie = 'cao' AND nom_fichier = %s
                """,
                (str(projet_id), nom_fichier),
            ).fetchall()
    except Exception as exc:
        logger.debug("Documents CAO non listés : %s", exc)
        return ids
    for extra in extras:
        did = extra.get("id")
        if did and did not in ids:
            ids.append(did)
    return ids


def _effacer_documents_dxf(ids: list[str]) -> None:
    if not ids:
        return
    from api.documents.crud_document import delete_document

    for did in ids:
        try:
            delete_document(UUID(did))
        except Exception as exc:
            logger.warning("Document DXF %s non retiré (bucket/BDD) : %s", did, exc)


def _assurer_geom_local_z(conn: psycopg.Connection) -> None:
    """geometry(Geometry, 0) est XY strict — les DXF conservent un Z."""
    row = conn.execute(
        """
        SELECT pg_catalog.format_type(a.atttypid, a.atttypmod) AS typ
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'bancarisation'
          AND c.relname = 'plan_cao_entite'
          AND a.attname = 'geom_local'
          AND NOT a.attisdropped
        """,
    ).fetchone()
    typ = (row["typ"] if row else "") or ""
    if not typ or typ == "geometry" or "GeometryZ" in typ or "geometryz" in typ.lower():
        return
    logger.info("Promotion geom_local vers geometry (XYZ) — était %s", typ)
    conn.execute(
        """
        ALTER TABLE bancarisation.plan_cao_entite
          ALTER COLUMN geom_local TYPE geometry
          USING geom_local
        """
    )


def _assurer_fk_calques(conn: psycopg.Connection) -> None:
    """FK composite ON DELETE SET NULL nullifiait plan_id (NOT NULL)."""
    row = conn.execute(
        """
        SELECT c.conname, pg_get_constraintdef(c.oid) AS def
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = rel.relnamespace
        WHERE n.nspname = 'bancarisation'
          AND rel.relname = 'plan_cao_calque'
          AND c.contype = 'f'
          AND pg_get_constraintdef(c.oid) ILIKE '%plan_cao_groupe%'
        """,
    ).fetchone()
    defn = (row["def"] if row else "") or ""
    if row and "SET NULL" not in defn.upper():
        return
    if row:
        logger.info("Remplacement FK calques→groupes SET NULL → RESTRICT (%s)", row["conname"])
        conn.execute(
            sql.SQL("ALTER TABLE bancarisation.plan_cao_calque DROP CONSTRAINT {}").format(
                sql.Identifier(row["conname"]),
            )
        )
    conn.execute(
        """
        ALTER TABLE bancarisation.plan_cao_calque
          ADD CONSTRAINT plan_cao_calque_groupe_fkey
          FOREIGN KEY (plan_id, groupe_id)
          REFERENCES bancarisation.plan_cao_groupe (plan_id, id)
          ON DELETE RESTRICT
          ON UPDATE CASCADE
        """
    )


def _verifier_projet(conn: psycopg.Connection, projet_id: UUID) -> None:
    row = conn.execute(
        "SELECT 1 FROM bancarisation.projets WHERE id = %s",
        (str(projet_id),),
    ).fetchone()
    if not row:
        raise PlanCaoError("Projet introuvable.")


def lister(projet_id: UUID) -> list[dict[str, Any]]:
    with _connect() as conn:
        _verifier_projet(conn, projet_id)
        try:
            rows = conn.execute(
                """
                SELECT id::text, nom_fichier, nb_entites, statut, calage_mode,
                       calque_0_inclus, dxf_version, insunits,
                       srid_declare, srid_declare_origine, multi_crs,
                       cree_le::text, modifie_le::text
                FROM bancarisation.plan_cao
                WHERE projet_id = %s
                ORDER BY cree_le DESC
                """,
                (str(projet_id),),
            ).fetchall()
        except UndefinedTable:
            return []
        except UndefinedColumn as exc:
            raise PlanCaoError(_MSG_CRS) from exc
        return [dict(r) for r in rows]


def charger(projet_id: UUID, plan_id: UUID) -> dict[str, Any]:
    with _connect() as conn:
        _verifier_projet(conn, projet_id)
        try:
            plan = conn.execute(
                """
                SELECT id::text, projet_id::text, document_id::text, nom_fichier,
                       dxf_version, insunits, facteur_metre, nb_entites, calque_0_inclus,
                       statut, calage_mode, srid_cible, tx, ty, rotation_rad, echelle,
                       srid_declare, srid_declare_origine, multi_crs, analyse_crs,
                       metadata,
                       ST_XMin(bbox_local) AS xmin, ST_YMin(bbox_local) AS ymin,
                       ST_XMax(bbox_local) AS xmax, ST_YMax(bbox_local) AS ymax
                FROM bancarisation.plan_cao
                WHERE id = %s AND projet_id = %s
                """,
                (str(plan_id), str(projet_id)),
            ).fetchone()
        except UndefinedTable as exc:
            raise PlanCaoError(_MSG_TABLES) from exc
        except UndefinedColumn as exc:
            raise PlanCaoError(_MSG_CRS) from exc
        if not plan:
            raise PlanCaoError("Plan CAO introuvable.")

        groupes_rows = conn.execute(
            """
            SELECT id, nom, ordre FROM bancarisation.plan_cao_groupe
            WHERE plan_id = %s ORDER BY ordre, nom
            """,
            (str(plan_id),),
        ).fetchall()
        calques_rows = conn.execute(
            """
            SELECT nom, groupe_id, nb_entites, types_dxf, visible,
                   couleur, aci, eteint, gele, verrouille,
                   porte_altimetrie, z_min, z_max,
                   srid_source, srid_origine, srid_confiance,
                   cluster_id, srid_ambigu
            FROM bancarisation.plan_cao_calque
            WHERE plan_id = %s
            ORDER BY nb_entites DESC
            """,
            (str(plan_id),),
        ).fetchall()
        geom_sql = (
            "ST_AsGeoJSON(ST_Force2D(COALESCE(geom, geom_local)))"
            if plan.get("calage_mode") == "srid_direct"
            else "ST_AsGeoJSON(ST_Force2D(geom_local))"
        )
        entites = conn.execute(
            f"""
            SELECT calque, dxf_type, handle, texte, bloc, attributs, couleur,
                   {geom_sql} AS geom,
                   ST_ZMax(geom_local) AS z
            FROM bancarisation.plan_cao_entite
            WHERE plan_id = %s
            """,
            (str(plan_id),),
        ).fetchall()

    bbox = None
    if plan["xmin"] is not None:
        bbox = {
            "xmin": float(plan["xmin"]),
            "ymin": float(plan["ymin"]),
            "xmax": float(plan["xmax"]),
            "ymax": float(plan["ymax"]),
        }
    meta_extra = plan["metadata"] if isinstance(plan["metadata"], dict) else {}
    profil = meta_extra.get("profil_z") or {}
    analyse_crs = plan.get("analyse_crs") if isinstance(plan.get("analyse_crs"), dict) else (
        meta_extra.get("analyse_crs") if isinstance(meta_extra.get("analyse_crs"), dict) else {}
    )
    par_srid_nom = {
        row["calque"]: row.get("crs_nom")
        for row in (analyse_crs.get("srid_par_calque") or [])
        if isinstance(row, dict)
    }
    calques = [_calque_api(r) for r in calques_rows]
    for c in calques:
        z = profil.get(c["nom"]) or {}
        if z:
            c["porte_altimetrie"] = c["porte_altimetrie"] or bool(z.get("porte_altimetrie"))
            c["probable_courbes_niveau"] = bool(z.get("probable_courbes_niveau"))
            if c.get("z_min") is None:
                c["z_min"] = z.get("z_min")
                c["z_max"] = z.get("z_max")
        if not c.get("crs_nom"):
            c["crs_nom"] = par_srid_nom.get(c["nom"])
    appartenance = {
        r["nom"]: r["groupe_id"]
        for r in calques_rows
        if r["groupe_id"]
    }
    features = []
    for i, e in enumerate(entites):
        geom = json.loads(e["geom"]) if e["geom"] else None
        if not geom:
            continue
        props: dict[str, Any] = {
            "calque": e["calque"],
            "dxf_type": e["dxf_type"],
            "handle": e["handle"],
        }
        if e["texte"]:
            props["texte"] = e["texte"]
        if e.get("bloc"):
            props["bloc"] = e["bloc"]
        attrs = e.get("attributs")
        if isinstance(attrs, dict) and attrs:
            props["attributs"] = attrs
        if e.get("couleur"):
            props["couleur"] = e["couleur"]
        zval = e.get("z")
        if zval is not None:
            try:
                props["z"] = float(zval)
            except (TypeError, ValueError):
                pass
        features.append({
            "type": "Feature",
            "id": i,
            "geometry": geom,
            "properties": props,
        })

    return {
        "plan_id": plan["id"],
        "projet_id": plan["projet_id"],
        "nom_fichier": plan["nom_fichier"],
        "nb_entites": plan["nb_entites"],
        "calque_0_inclus": plan["calque_0_inclus"],
        "metadata": {
            "dxf_version": plan["dxf_version"],
            "insunits": plan["insunits"],
            "facteur_applique": plan["facteur_metre"],
            "unite_sortie": meta_extra.get("unite_sortie", "unite_dessin"),
            "entites_non_converties": meta_extra.get("entites_non_converties") or {},
            "bbox_locale": bbox,
            "largeur": (bbox["xmax"] - bbox["xmin"]) if bbox else None,
            "hauteur": (bbox["ymax"] - bbox["ymin"]) if bbox else None,
            "types_3d": meta_extra.get("types_3d") or {},
            "profil_z": meta_extra.get("profil_z") or {},
            "blocs": meta_extra.get("blocs") or {},
            "nb_entites_avec_attributs": meta_extra.get("nb_entites_avec_attributs") or 0,
        },
        "calques": calques,
        "calques_masques": meta_extra.get("calques_masques") or {},
        "groupes": {
            "groupes": [{"id": g["id"], "nom": g["nom"]} for g in groupes_rows],
            "appartenance": appartenance,
        },
        "visibles": [r["nom"] for r in calques_rows if r["visible"]],
        "geojson": {"type": "FeatureCollection", "features": features},
        "rasters": meta_extra.get("rasters") or [],
        "rasters_resume": meta_extra.get("rasters_resume") or {},
        "calage": {
            "mode": plan["calage_mode"],
            "srid_cible": plan["srid_cible"],
            "tx": float(plan["tx"] or 0),
            "ty": float(plan["ty"] or 0),
            "rotation_rad": float(plan["rotation_rad"] or 0),
            "echelle": float(plan["echelle"] or 1),
            "paires": (meta_extra.get("calage") or {}).get("paires") or [],
        },
        "crs": {
            "srid_declare": plan.get("srid_declare"),
            "srid_declare_origine": plan.get("srid_declare_origine"),
            "multi_crs": bool(plan.get("multi_crs")),
            "analyse": analyse_crs or None,
        },
    }


def _ecrire_groupes(
    conn: psycopg.Connection,
    plan_id: str,
    groupes: dict[str, Any],
    calques: list[dict[str, Any]],
    visibles: set[str] | None,
) -> None:
    # Détacher avant DELETE groupes : la FK composite ON DELETE SET NULL
    # nullifie aussi plan_id (NOT NULL) → 500.
    conn.execute(
        "UPDATE bancarisation.plan_cao_calque SET groupe_id = NULL WHERE plan_id = %s",
        (plan_id,),
    )
    conn.execute(
        "DELETE FROM bancarisation.plan_cao_calque WHERE plan_id = %s",
        (plan_id,),
    )
    conn.execute(
        "DELETE FROM bancarisation.plan_cao_groupe WHERE plan_id = %s",
        (plan_id,),
    )
    for i, g in enumerate(groupes.get("groupes") or []):
        conn.execute(
            """
            INSERT INTO bancarisation.plan_cao_groupe (plan_id, id, nom, ordre)
            VALUES (%s, %s, %s, %s)
            """,
            (plan_id, g["id"], g["nom"], i),
        )
    appartenance = groupes.get("appartenance") or {}
    for c in calques:
        gid = appartenance.get(c["nom"])
        vis = True if visibles is None else c["nom"] in visibles
        if visibles is None:
            vis = bool(c.get("visible_defaut", True))
        conn.execute(
            """
            INSERT INTO bancarisation.plan_cao_calque
              (plan_id, nom, groupe_id, nb_entites, types_dxf, visible,
               couleur, aci, eteint, gele, verrouille,
               porte_altimetrie, z_min, z_max,
               srid_source, srid_origine, srid_confiance, cluster_id, srid_ambigu)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
            """,
            (
                plan_id,
                c["nom"],
                gid,
                c.get("nb") or c.get("nb_entites") or 0,
                Jsonb(c.get("types") or {}),
                vis,
                c.get("couleur"),
                c.get("aci"),
                bool(c.get("eteint")),
                bool(c.get("gele")),
                bool(c.get("verrouille")),
                bool(c.get("porte_altimetrie")),
                c.get("z_min"),
                c.get("z_max"),
                c.get("srid_source"),
                c.get("srid_origine") or "herite",
                c.get("srid_confiance"),
                c.get("cluster_id"),
                bool(c.get("srid_ambigu")),
            ),
        )


def enregistrer_groupes(
    projet_id: UUID,
    plan_id: UUID,
    groupes: dict[str, Any],
    visibles: list[str] | None,
) -> None:
    with _connect() as conn:
        _verifier_projet(conn, projet_id)
        try:
            _assurer_fk_calques(conn)
            plan = conn.execute(
                """
                SELECT 1 FROM bancarisation.plan_cao
                WHERE id = %s AND projet_id = %s
                FOR UPDATE
                """,
                (str(plan_id), str(projet_id)),
            ).fetchone()
        except UndefinedTable as exc:
            raise PlanCaoError(_MSG_TABLES) from exc
        if not plan:
            raise PlanCaoError("Plan CAO introuvable.")
        calques = conn.execute(
            """
            SELECT nom, nb_entites, types_dxf, visible,
                   couleur, aci, eteint, gele, verrouille,
                   porte_altimetrie, z_min, z_max,
                   srid_source, srid_origine, srid_confiance,
                   cluster_id, srid_ambigu
            FROM bancarisation.plan_cao_calque WHERE plan_id = %s
            """,
            (str(plan_id),),
        ).fetchall()
        payload = [_calque_api(dict(r)) for r in calques]
        vis_set = set(visibles) if visibles is not None else None
        try:
            _ecrire_groupes(conn, str(plan_id), groupes, payload, vis_set)
        except Exception as exc:
            raise PlanCaoError(f"Enregistrement des groupes impossible : {exc}") from exc
        conn.execute(
            "UPDATE bancarisation.plan_cao SET modifie_le = now() WHERE id = %s",
            (str(plan_id),),
        )
        conn.commit()


def enregistrer_rasters(
    projet_id: UUID,
    plan_id: UUID,
    rasters: list[dict[str, Any]],
    resume: dict[str, Any],
) -> None:
    with _connect() as conn:
        _verifier_projet(conn, projet_id)
        try:
            row = conn.execute(
                """
                SELECT metadata FROM bancarisation.plan_cao
                WHERE id = %s AND projet_id = %s
                """,
                (str(plan_id), str(projet_id)),
            ).fetchone()
        except UndefinedTable as exc:
            raise PlanCaoError(_MSG_TABLES) from exc
        if not row:
            raise PlanCaoError("Plan CAO introuvable.")
        meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
        meta["rasters"] = rasters
        meta["rasters_resume"] = resume
        conn.execute(
            """
            UPDATE bancarisation.plan_cao
            SET metadata = %s, modifie_le = now()
            WHERE id = %s
            """,
            (Jsonb(meta), str(plan_id)),
        )
        conn.commit()


def supprimer(projet_id: UUID, plan_id: UUID) -> dict[str, Any]:
    """Supprime le plan CAO et tout ce qui s'y rattache (PostGIS + fichier DXF).

    Tables 035/037 : plan_cao_entite (bloc, attributs, couleur, geom, geom_3857),
    plan_cao_calque, plan_cao_groupe, plan_cao (calage + metadata rasters).
    Le DXF dans `documents` n'est pas en CASCADE (FK SET NULL) : on le retire
    du bucket ensuite.
    """
    with _connect() as conn:
        _verifier_projet(conn, projet_id)
        try:
            row = conn.execute(
                """
                SELECT id::text, document_id::text, nom_fichier
                FROM bancarisation.plan_cao
                WHERE id = %s AND projet_id = %s
                """,
                (str(plan_id), str(projet_id)),
            ).fetchone()
        except UndefinedTable as exc:
            raise PlanCaoError(_MSG_TABLES) from exc
        if not row:
            raise PlanCaoError("Plan CAO introuvable.")
        nom_fichier = row["nom_fichier"]
        docs = _ids_documents_cao(
            conn, projet_id, nom_fichier, row.get("document_id"),
        )
        _vider_plan(conn, str(plan_id))
        conn.commit()

    _effacer_documents_dxf(docs)
    logger.info(
        "Plan CAO %s (%s) supprimé — %s document(s) DXF",
        plan_id, nom_fichier, len(docs),
    )
    return {"ok": True, "nom_fichier": nom_fichier}


def persister(
    projet_id: UUID,
    *,
    nom_fichier: str,
    document_id: UUID | None,
    collection: dict[str, Any],
    calques: list[dict[str, Any]],
    calques_masques: dict[str, int],
    calque_0_inclus: bool,
    groupes: dict[str, Any] | None = None,
    rasters: list[dict[str, Any]] | None = None,
    rasters_resume: dict[str, Any] | None = None,
    analyse_crs: dict[str, Any] | None = None,
    srid_declare: int | None = None,
    srid_declare_origine: str | None = None,
    multi_crs: bool = False,
) -> str:
    """Écrit (ou remplace) le plan du même nom de fichier. Retourne l'id."""
    meta = collection.get("metadata") or {}
    features = collection.get("features") or []
    bbox = meta.get("bbox_locale")
    wkt = _bbox_wkt(bbox)

    with _connect() as conn:
        try:
            _verifier_projet(conn, projet_id)
            _assurer_geom_local_z(conn)
            _assurer_fk_calques(conn)
            exist = conn.execute(
                """
                SELECT id::text, document_id::text FROM bancarisation.plan_cao
                WHERE projet_id = %s AND nom_fichier = %s
                """,
                (str(projet_id), nom_fichier),
            ).fetchone()
            visibles_gardes: set[str] | None = None
            docs_anciens: list[str] = []
            if exist:
                if groupes is None:
                    anciens, visibles_gardes, noms_anciens = _lire_rangement(conn, exist["id"])
                    if anciens:
                        groupes = _fusionner_groupes(anciens, calques)
                    if visibles_gardes is not None:
                        for c in calques:
                            if c["nom"] not in noms_anciens and c.get("visible_defaut", True):
                                visibles_gardes.add(c["nom"])
                docs_anciens = [
                    d for d in _ids_documents_cao(
                        conn, projet_id, nom_fichier, exist.get("document_id"),
                    )
                    if d != (str(document_id) if document_id else "")
                ]
                _vider_plan(conn, exist["id"])
            groupes = groupes or groupes_suggerees(calques)

            plan_id = conn.execute(
                """
                INSERT INTO bancarisation.plan_cao (
                  projet_id, document_id, nom_fichier, dxf_version, insunits,
                  facteur_metre, nb_entites, bbox_local, calque_0_inclus, statut,
                  srid_declare, srid_declare_origine, multi_crs, analyse_crs,
                  metadata
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, ST_SetSRID(ST_GeomFromText(%s), 0), %s, 'analyse',
                  %s, %s, %s, %s,
                  %s
                )
                RETURNING id::text
                """,
                (
                    str(projet_id),
                    str(document_id) if document_id else None,
                    nom_fichier,
                    meta.get("dxf_version"),
                    meta.get("insunits"),
                    meta.get("facteur_applique"),
                    len(features),
                    wkt,
                    calque_0_inclus,
                    srid_declare,
                    srid_declare_origine,
                    bool(multi_crs),
                    Jsonb(analyse_crs or meta.get("analyse_crs") or {}),
                    Jsonb({
                        "unite_sortie": meta.get("unite_sortie"),
                        "entites_non_converties": meta.get("entites_non_converties") or {},
                        "calques_masques": calques_masques,
                        "rasters": rasters or [],
                        "rasters_resume": rasters_resume or {},
                        "types_3d": meta.get("types_3d") or {},
                        "profil_z": meta.get("profil_z") or {},
                        "blocs": meta.get("blocs") or {},
                        "nb_entites_avec_attributs": meta.get("nb_entites_avec_attributs") or 0,
                    }),
                ),
            ).fetchone()
            assert plan_id
            pid = plan_id["id"]
            _ecrire_groupes(conn, pid, groupes, calques, visibles_gardes)

            sql_entite = """
                INSERT INTO bancarisation.plan_cao_entite
                  (plan_id, calque, dxf_type, handle, texte, bloc, attributs, couleur, geom_local)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 0))
            """
            batch: list[tuple[Any, ...]] = []
            with conn.cursor() as cur:
                for feat in features:
                    geom = feat.get("geometry")
                    props = feat.get("properties") or {}
                    if not isinstance(geom, dict):
                        continue
                    attrs = props.get("attributs") if isinstance(props.get("attributs"), dict) else {}
                    batch.append((
                        pid,
                        str(props.get("calque") or "0"),
                        str(props.get("dxf_type") or ""),
                        props.get("handle"),
                        props.get("texte"),
                        props.get("bloc"),
                        Jsonb(attrs or {}),
                        props.get("couleur"),
                        json.dumps(geom),
                    ))
                    if len(batch) >= 800:
                        cur.executemany(sql_entite, batch)
                        batch = []
                if batch:
                    cur.executemany(sql_entite, batch)
            conn.commit()
            _effacer_documents_dxf(docs_anciens)
            return pid
        except UndefinedTable as exc:
            raise PlanCaoError(_MSG_TABLES) from exc
        except UndefinedColumn as exc:
            raise PlanCaoError(_MSG_CRS) from exc
        except InvalidParameterValue as exc:
            raise PlanCaoError(_MSG_GEOM_Z) from exc
        except PlanCaoError:
            raise
        except Exception as exc:
            raise PlanCaoError(f"Enregistrement PostGIS impossible : {exc}") from exc


def appliquer_calage(
    projet_id: UUID,
    plan_id: UUID,
    *,
    mode: str,
    tx: float,
    ty: float,
    rotation_rad: float,
    echelle: float,
    srid_cible: int = 2154,
    paires: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Écrit la similitude, puis reprojette chaque entité depuis le CRS de son calque.

    srid_direct : ST_Transform(geom_local, srid_source du calque → 2154).
    deux_points / manuel : ST_Affine (Helmert) vers 2154.
    """
    if mode not in ("deux_points", "srid_direct", "manuel"):
        raise PlanCaoError(f"Mode de calage inconnu : {mode}")
    if srid_cible != 2154:
        raise PlanCaoError("Seul le SRID 2154 est pris en charge pour l’instant.")

    from api.carto.calage import coeffs_affine

    if mode == "srid_direct":
        tx, ty, rotation_rad, echelle = 0.0, 0.0, 0.0, 1.0

    a, b, d, e, xoff, yoff = coeffs_affine(tx, ty, rotation_rad, echelle)

    with _connect() as conn:
        _verifier_projet(conn, projet_id)
        try:
            row = conn.execute(
                """
                SELECT id::text, metadata FROM bancarisation.plan_cao
                WHERE id = %s AND projet_id = %s
                """,
                (str(plan_id), str(projet_id)),
            ).fetchone()
        except UndefinedTable as exc:
            raise PlanCaoError(_MSG_TABLES) from exc
        if not row:
            raise PlanCaoError("Plan CAO introuvable.")

        meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
        meta["calage"] = {
            "mode": mode,
            "paires": paires or [],
            "srid_cible": srid_cible,
        }
        conn.execute(
            """
            UPDATE bancarisation.plan_cao
            SET calage_mode = %s, srid_cible = %s,
                tx = %s, ty = %s, rotation_rad = %s, echelle = %s,
                statut = 'cale', metadata = %s, modifie_le = now()
            WHERE id = %s
            """,
            (mode, srid_cible, tx, ty, rotation_rad, echelle, Jsonb(meta), str(plan_id)),
        )
        try:
            conn.execute(
                "SELECT bancarisation.appliquer_calage_plan(%s)",
                (str(plan_id),),
            )
        except UndefinedFunction:
            logger.warning("appliquer_calage_plan absente — fallback Helmert (lancer 040_plan_cao_srid_calque.sql)")
            conn.execute(
                """
                UPDATE bancarisation.plan_cao_entite
                SET
                  geom = ST_SetSRID(
                    ST_MakeValid(ST_Affine(ST_Force2D(geom_local), %s, %s, %s, %s, %s, %s)),
                    2154
                  ),
                  geom_3857 = ST_Transform(
                    ST_SetSRID(
                      ST_MakeValid(ST_Affine(ST_Force2D(geom_local), %s, %s, %s, %s, %s, %s)),
                      2154
                    ),
                    3857
                  )
                WHERE plan_id = %s
                """,
                (a, b, d, e, xoff, yoff, a, b, d, e, xoff, yoff, str(plan_id)),
            )
        conn.commit()
    return charger(projet_id, plan_id)


def enregistrer_srid_calques(
    projet_id: UUID,
    plan_id: UUID,
    attributions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Enregistre le CRS choisi par l'utilisateur, calque par calque (origine=user)."""
    if not attributions:
        raise PlanCaoError("Aucune attribution CRS.")
    with _connect() as conn:
        _verifier_projet(conn, projet_id)
        try:
            row = conn.execute(
                """
                SELECT 1 FROM bancarisation.plan_cao
                WHERE id = %s AND projet_id = %s
                """,
                (str(plan_id), str(projet_id)),
            ).fetchone()
        except UndefinedTable as exc:
            raise PlanCaoError(_MSG_TABLES) from exc
        except UndefinedColumn as exc:
            raise PlanCaoError(_MSG_CRS) from exc
        if not row:
            raise PlanCaoError("Plan CAO introuvable.")
        for item in attributions:
            nom = str(item.get("nom") or "").strip()
            if not nom:
                continue
            srid = item.get("srid_source")
            srid_int = int(srid) if srid not in (None, "", 0, "0") else None
            conn.execute(
                """
                UPDATE bancarisation.plan_cao_calque
                SET srid_source = %s,
                    srid_origine = 'user',
                    srid_ambigu = false,
                    srid_confiance = CASE WHEN %s IS NULL THEN 'inconnue' ELSE 'haute' END
                WHERE plan_id = %s AND nom = %s
                """,
                (srid_int, srid_int, str(plan_id), nom),
            )
        conn.execute(
            "UPDATE bancarisation.plan_cao SET modifie_le = now() WHERE id = %s",
            (str(plan_id),),
        )
        conn.commit()
    return charger(projet_id, plan_id)


def latitude_projet(projet_id: UUID) -> float | None:
    """Latitude WGS84 du centroïde des UG, pour pré-sélectionner la zone CC."""
    try:
        with _connect() as conn:
            _verifier_projet(conn, projet_id)
            row = conn.execute(
                """
                SELECT ST_Y(ST_Transform(ST_Centroid(ST_Collect(g)), 4326)) AS lat
                FROM (
                  SELECT geom_3857 AS g FROM bancarisation.unites_de_gestion_surf
                   WHERE projet_id = %s AND geom_3857 IS NOT NULL
                  UNION ALL
                  SELECT geom_3857 FROM bancarisation.unites_de_gestion_lin
                   WHERE projet_id = %s AND geom_3857 IS NOT NULL
                  UNION ALL
                  SELECT geom_3857 FROM bancarisation.unites_de_gestion_pct
                   WHERE projet_id = %s AND geom_3857 IS NOT NULL
                ) s
                """,
                (str(projet_id), str(projet_id), str(projet_id)),
            ).fetchone()
            if not row or row.get("lat") is None:
                return None
            return float(row["lat"])
    except PlanCaoError:
        raise
    except Exception as exc:
        logger.debug("Latitude projet indisponible : %s", exc)
        return None
