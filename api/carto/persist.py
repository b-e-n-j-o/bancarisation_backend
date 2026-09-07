"""Persistance PostGIS des plans CAO (aperçu local, non calé)."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any
from uuid import UUID

import psycopg
from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.db.env import get_database_url

_MSG_TABLES = (
    "Tables plan_cao absentes — appliquer backend/api/ocr/db/sql/035_plan_cao.sql"
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
                       cree_le::text, modifie_le::text
                FROM bancarisation.plan_cao
                WHERE projet_id = %s
                ORDER BY cree_le DESC
                """,
                (str(projet_id),),
            ).fetchall()
        except UndefinedTable:
            return []
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
            SELECT nom, groupe_id, nb_entites, types_dxf, visible
            FROM bancarisation.plan_cao_calque
            WHERE plan_id = %s
            ORDER BY nb_entites DESC
            """,
            (str(plan_id),),
        ).fetchall()
        entites = conn.execute(
            """
            SELECT calque, dxf_type, handle, texte,
                   ST_AsGeoJSON(geom_local) AS geom
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
    calques = [
        {
            "nom": r["nom"],
            "nb": r["nb_entites"],
            "types": r["types_dxf"] if isinstance(r["types_dxf"], dict) else {},
            "visible_defaut": r["visible"],
        }
        for r in calques_rows
    ]
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
    }


def _ecrire_groupes(
    conn: psycopg.Connection,
    plan_id: str,
    groupes: dict[str, Any],
    calques: list[dict[str, Any]],
    visibles: set[str] | None,
) -> None:
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
              (plan_id, nom, groupe_id, nb_entites, types_dxf, visible)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                plan_id,
                c["nom"],
                gid,
                c.get("nb") or c.get("nb_entites") or 0,
                Jsonb(c.get("types") or {}),
                vis,
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
            plan = conn.execute(
                """
                SELECT 1 FROM bancarisation.plan_cao
                WHERE id = %s AND projet_id = %s
                """,
                (str(plan_id), str(projet_id)),
            ).fetchone()
        except UndefinedTable as exc:
            raise PlanCaoError(_MSG_TABLES) from exc
        if not plan:
            raise PlanCaoError("Plan CAO introuvable.")
        calques = conn.execute(
            """
            SELECT nom, nb_entites, types_dxf, visible
            FROM bancarisation.plan_cao_calque WHERE plan_id = %s
            """,
            (str(plan_id),),
        ).fetchall()
        payload = [
            {
                "nom": r["nom"],
                "nb": r["nb_entites"],
                "types": r["types_dxf"] if isinstance(r["types_dxf"], dict) else {},
                "visible_defaut": r["visible"],
            }
            for r in calques
        ]
        vis_set = set(visibles) if visibles is not None else None
        _ecrire_groupes(conn, str(plan_id), groupes, payload, vis_set)
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
) -> str:
    """Écrit (ou remplace) le plan du même nom de fichier. Retourne l'id."""
    meta = collection.get("metadata") or {}
    features = collection.get("features") or []
    bbox = meta.get("bbox_locale")
    wkt = _bbox_wkt(bbox)

    with _connect() as conn:
        try:
            _verifier_projet(conn, projet_id)
            exist = conn.execute(
                """
                SELECT id::text FROM bancarisation.plan_cao
                WHERE projet_id = %s AND nom_fichier = %s
                """,
                (str(projet_id), nom_fichier),
            ).fetchone()
            visibles_gardes: set[str] | None = None
            if exist:
                if groupes is None:
                    anciens, visibles_gardes, noms_anciens = _lire_rangement(conn, exist["id"])
                    if anciens:
                        groupes = _fusionner_groupes(anciens, calques)
                    if visibles_gardes is not None:
                        for c in calques:
                            if c["nom"] not in noms_anciens and c.get("visible_defaut", True):
                                visibles_gardes.add(c["nom"])
                conn.execute(
                    "DELETE FROM bancarisation.plan_cao WHERE id = %s",
                    (exist["id"],),
                )
            groupes = groupes or groupes_suggerees(calques)

            plan_id = conn.execute(
                """
                INSERT INTO bancarisation.plan_cao (
                  projet_id, document_id, nom_fichier, dxf_version, insunits,
                  facteur_metre, nb_entites, bbox_local, calque_0_inclus, statut,
                  metadata
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, ST_SetSRID(ST_GeomFromText(%s), 0), %s, 'analyse',
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
                    Jsonb({
                        "unite_sortie": meta.get("unite_sortie"),
                        "entites_non_converties": meta.get("entites_non_converties") or {},
                        "calques_masques": calques_masques,
                        "rasters": rasters or [],
                        "rasters_resume": rasters_resume or {},
                    }),
                ),
            ).fetchone()
            assert plan_id
            pid = plan_id["id"]
            _ecrire_groupes(conn, pid, groupes, calques, visibles_gardes)

            sql_entite = """
                INSERT INTO bancarisation.plan_cao_entite
                  (plan_id, calque, dxf_type, handle, texte, geom_local)
                VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 0))
            """
            batch: list[tuple[Any, ...]] = []
            with conn.cursor() as cur:
                for feat in features:
                    geom = feat.get("geometry")
                    props = feat.get("properties") or {}
                    if not isinstance(geom, dict):
                        continue
                    batch.append((
                        pid,
                        str(props.get("calque") or "0"),
                        str(props.get("dxf_type") or ""),
                        props.get("handle"),
                        props.get("texte"),
                        json.dumps(geom),
                    ))
                    if len(batch) >= 800:
                        cur.executemany(sql_entite, batch)
                        batch = []
                if batch:
                    cur.executemany(sql_entite, batch)
            conn.commit()
            return pid
        except UndefinedTable as exc:
            raise PlanCaoError(_MSG_TABLES) from exc


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
    """Écrit la similitude, ST_Affine geom_local → geom (2154) + geom_3857."""
    if mode not in ("deux_points", "srid_direct", "manuel"):
        raise PlanCaoError(f"Mode de calage inconnu : {mode}")
    if srid_cible != 2154:
        raise PlanCaoError("Seul le SRID 2154 est pris en charge pour l’instant.")

    from api.carto.calage import coeffs_affine

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
