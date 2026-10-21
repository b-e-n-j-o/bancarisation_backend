"""04_Geometries : GeoPackage 2154, GeoJSON 4326, DXF d'origine et calage."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg

from api.export.contexte import ExportContexte, FichierExport
from api.export.modules._fichiers import fichier
from api.export.modules.documents import telecharger_binaire
from api.export.nommage import slug
from api.export.sqlutil import ecrire_json, lignes, table_existe

nom = "geometries"
dossier = "04_Geometries"

# (couche, table, colonne géométrique, filtre SQL additionnel)
COUCHES: list[tuple[str, str, str]] = [
    ("emprise_projet", "emprise_projet", "geom"),
    ("ug_surf", "unites_de_gestion_surf", "geom"),
    ("ug_lin", "unites_de_gestion_lin", "geom"),
    ("ug_pct", "unites_de_gestion_pct", "geom"),
    ("parcelles_foncier", "parcelles", "geom"),
]

EXCLUES = {
    "geomce_lin", "geomce_pct", "geomce_surf", "geomce_communes",
    "satellite_captures", "export_geomce",
}
COLONNES_GEOM_IGNOREES = {"geom_3857", "geom_4326", "geom_local", "bbox_local", "geom_intersection"}


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    pid = str(ctx.projet_id)
    couches = list(COUCHES) + _couches_decouvertes(conn, ctx)
    produits: list[FichierExport] = []
    gpkg = fichier(ctx, f"{dossier}/projet.gpkg", nom, "Géométries du projet (Lambert 93)", compresser=False)
    nb_couches = 0
    for couche, table, colonne in couches:
        n = _ecrire_couche(ctx, conn, pid, couche, table, colonne, gpkg.chemin_local)
        if n:
            nb_couches += 1
            _geojson(ctx, conn, pid, couche, table, colonne, produits)
        else:
            ctx.avertir(f"Couche {couche} vide, non créée.")
    if nb_couches:
        gpkg.nb_elements = nb_couches
        produits.insert(0, gpkg)
    elif gpkg.chemin_local.exists():
        gpkg.chemin_local.unlink()
    produits.extend(_cao(ctx, conn, pid))
    return produits


def _couches_decouvertes(conn, ctx: ExportContexte) -> list[tuple[str, str, str]]:
    connues = {table for _, table, _ in COUCHES} | {"plan_cao_entite"}
    try:
        with conn.transaction():
            rows = lignes(
                conn,
                """
                select f_table_name as table_name, f_geometry_column as colonne, srid
                  from geometry_columns
                 where f_table_schema = 'bancarisation'
                """,
            )
    except Exception as exc:
        ctx.avertir(f"Inventaire geometry_columns indisponible : {exc}")
        return [("cao_entites", "plan_cao_entite", "geom")]
    extra = [("cao_entites", "plan_cao_entite", "geom")]
    for row in rows:
        table = row["table_name"]
        colonne = row["colonne"]
        if table in EXCLUES or table in connues or colonne in COLONNES_GEOM_IGNOREES:
            continue
        if int(row.get("srid") or 0) == 0:
            continue
        if colonne != "geom":
            continue
        if not _a_projet(conn, table):
            ctx.avertir(f"{table}.{colonne} : pas de projet_id, couche ignorée.")
            continue
        extra.append((slug(table, 40).replace("-", "_"), table, colonne))
        connues.add(table)
    return extra


def _a_projet(conn, table: str) -> bool:
    row = lignes(
        conn,
        """
        select 1 as ok from information_schema.columns
         where table_schema = 'bancarisation' and table_name = %s and column_name = 'projet_id'
        """,
        (table,),
    )
    return bool(row)


def _attributs(conn, table: str) -> list[str]:
    rows = lignes(
        conn,
        """
        select column_name, udt_name
          from information_schema.columns
         where table_schema = 'bancarisation' and table_name = %s
         order by ordinal_position
        """,
        (table,),
    )
    return [r["column_name"] for r in rows if r["udt_name"] != "geometry"][:40]


def _ecrire_couche(ctx, conn, pid, couche, table, colonne, gpkg: Path) -> int:
    if not table_existe(conn, table):
        ctx.avertir(f"Table {table} absente, couche {couche} ignorée.")
        return 0
    attrs = _attributs(conn, table)
    select_attrs = ", ".join(f't."{a}"' for a in attrs) or "t.projet_id"
    where = _filtre(table)
    try:
        with conn.transaction():
            rows = lignes(
                conn,
                f"""
                select {select_attrs}, ST_AsBinary(t."{colonne}") as _wkb
                  from bancarisation."{table}" t
                 where {where}
                   and t."{colonne}" is not null
                """,
                (pid,),
            )
    except Exception as exc:
        ctx.avertir(f"Couche {couche} illisible : {exc}")
        return 0
    if not rows:
        return 0
    if couche.startswith("ug_"):
        _ajouter_mesures(conn, pid, rows)
        if "mesures_liees" not in attrs:
            attrs.append("mesures_liees")
    _ecrire_gpkg(gpkg, couche, rows, attrs)
    return len(rows)


def _filtre(table: str) -> str:
    if table == "plan_cao_entite":
        return "t.projet_id = %s"
    if table == "parcelles":
        return "t.projet_id = %s"
    return "t.projet_id = %s"


def _ajouter_mesures(conn, pid: str, rows: list[dict]) -> None:
    mesures = lignes(
        conn,
        """
        select code, titre, ug_ids
          from bancarisation.action_fiche
         where projet_id = %s
        """,
        (pid,),
    )
    for row in rows:
        code = str(row.get("ug_id") or "")
        lies = [
            " ".join(p for p in (m.get("code"), m.get("titre")) if p).strip()
            for m in mesures
            if code and code in (m.get("ug_ids") or [])
        ]
        row["mesures_liees"] = " | ".join(lies)


def _ecrire_gpkg(gpkg: Path, couche: str, rows: list[dict], attrs: list[str]) -> None:
    import numpy as np
    import pyogrio

    geoms = []
    colonnes = {a: [] for a in attrs}
    for row in rows:
        brut = row.get("_wkb")
        if not brut:
            continue
        geoms.append(bytes(brut))
        for a in attrs:
            valeur = row.get(a)
            if isinstance(valeur, (dict, list)):
                valeur = json.dumps(valeur, ensure_ascii=False, default=str)
            elif valeur is not None and not isinstance(valeur, (int, float, str)):
                valeur = str(valeur)
            colonnes[a].append(valeur)
    if not geoms:
        return
    types = {_type_wkb(geom) for geom in geoms}
    geometry_type = next(iter(types)) if len(types) == 1 else "Unknown"
    pyogrio.raw.write(
        gpkg,
        np.array(geoms, dtype=object),
        [np.array(colonnes[a], dtype=object) for a in attrs],
        attrs,
        layer=couche[:60],
        driver="GPKG",
        geometry_type=geometry_type,
        crs="EPSG:2154",
        append=gpkg.exists(),
    )


def _type_wkb(brut: bytes) -> str:
    import struct

    endian = "<" if brut[0] == 1 else ">"
    code = struct.unpack(endian + "I", brut[1:5])[0] & 0xFF
    return {
        1: "Point",
        2: "LineString",
        3: "Polygon",
        4: "MultiPoint",
        5: "MultiLineString",
        6: "MultiPolygon",
    }.get(code, "Unknown")


def _geojson(ctx, conn, pid, couche, table, colonne, produits: list[FichierExport]) -> None:
    attrs = _attributs(conn, table)
    proprietes = (
        "json_build_object(" + ", ".join(f"'{a}', t.\"{a}\"" for a in attrs) + ")"
        if attrs else "'{}'::json"
    )
    try:
        with conn.transaction():
            row = lignes(
                conn,
                f"""
                select json_build_object(
                  'type', 'FeatureCollection',
                  'features', coalesce(json_agg(
                    json_build_object(
                      'type', 'Feature',
                      'geometry', ST_AsGeoJSON(ST_Transform(t."{colonne}", 4326))::json,
                      'properties', {proprietes}
                    )
                  ), '[]'::json)
                )::text as geojson
                  from bancarisation."{table}" t
                 where t.projet_id = %s and t."{colonne}" is not null
                """,
                (pid,),
            )
    except Exception as exc:
        ctx.avertir(f"GeoJSON {couche} : {exc}")
        return
    if not row or not row[0].get("geojson"):
        return
    fic = fichier(
        ctx,
        f"{dossier}/geojson/{couche}.geojson",
        nom,
        f"Couche {couche} en WGS84",
        compresser=True,
    )
    fic.chemin_local.write_text(row[0]["geojson"], encoding="utf-8")
    produits.append(fic)


def _cao(ctx, conn, pid: str) -> list[FichierExport]:
    if not table_existe(conn, "plan_cao"):
        ctx.avertir("Plans CAO : table plan_cao absente.")
        return []
    plans = lignes(
        conn,
        """
        select p.id::text, p.nom_fichier, p.document_id::text, p.calage_mode,
               p.srid_cible, p.tx, p.ty, p.rotation_rad, p.echelle, p.metadata,
               d.bucket_path, d.nom_fichier as doc_nom
          from bancarisation.plan_cao p
          left join bancarisation.documents d on d.id = p.document_id
         where p.projet_id = %s
        """,
        (pid,),
    )
    produits = []
    for plan in plans:
        stem = slug(Path(plan.get("nom_fichier") or "plan").stem, 40)
        meta = plan.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        calage = {
            "plan_id": plan["id"],
            "nom_fichier": plan.get("nom_fichier"),
            "calage_mode": plan.get("calage_mode"),
            "srid_cible": plan.get("srid_cible"),
            "tx": plan.get("tx"),
            "ty": plan.get("ty"),
            "rotation_rad": plan.get("rotation_rad"),
            "echelle": plan.get("echelle"),
            "paires": ((meta.get("calage") or {}).get("paires") if isinstance(meta, dict) else []) or [],
        }
        fic = fichier(ctx, f"{dossier}/cao/{stem}_calage.json", nom, "Paramètres de calage", compresser=True)
        ecrire_json(fic.chemin_local, calage)
        produits.append(fic)
        chemin = plan.get("bucket_path")
        if not chemin:
            ctx.avertir(f"DXF {plan.get('nom_fichier')} : pas de fichier d'origine dans documents.")
            continue
        dest = fichier(
            ctx,
            f"{dossier}/cao/{stem}.dxf",
            nom,
            "DXF d'origine",
            compresser=True,
        )
        try:
            telecharger_binaire(chemin, dest.chemin_local)
        except Exception as exc:
            ctx.avertir(f"DXF {plan.get('nom_fichier')} introuvable : {exc}")
            if dest.chemin_local.exists():
                dest.chemin_local.unlink()
            continue
        produits.append(dest)
    return produits
