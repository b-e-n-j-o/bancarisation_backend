"""Génération ZIP shapefile conforme gabarit GéoMCE."""

from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import transform, unary_union
from pyproj import Transformer

from api.geomce.constantes import (
    DBF_FIELD_NAMES,
    DBF_SCHEMA,
    ENCODING,
    NOM_FICHIER_MAX,
    SRID_AIRE,
    STRATEGIE_GEOM_DEFAUT,
)
from api.geomce.validation import build_attributs, resolve_srid


def slugify(text: str, max_len: int = NOM_FICHIER_MAX) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return (cleaned or "export")[:max_len]


def _to_polygons(geom: Any) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    g = make_valid(geom)
    if isinstance(g, Polygon):
        return [g] if not g.is_empty else []
    if isinstance(g, MultiPolygon):
        return [p for p in g.geoms if isinstance(p, Polygon) and not p.is_empty]
    # GeometryCollection etc.
    out: list[Polygon] = []
    if hasattr(g, "geoms"):
        for part in g.geoms:
            out.extend(_to_polygons(part))
    return out


def load_polygons_from_features(
    features: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Polygon], bool]:
    """Retourne (meta_geoms pour contrôle, polygones, au_moins_une_reparee)."""
    meta: list[dict[str, Any]] = []
    polys: list[Polygon] = []
    reparee = False
    for f in features:
        props = f.get("properties") or {}
        geom_geojson = f.get("geometry")
        if not geom_geojson:
            continue
        try:
            g = shape(geom_geojson)
        except Exception:
            meta.append(
                {
                    "libelle": props.get("libelle"),
                    "ug_id": props.get("ug_id"),
                    "type_geom": "invalide",
                    "invalide": True,
                }
            )
            continue

        gtype = g.geom_type.lower()
        was_valid = g.is_valid
        parts = _to_polygons(g)
        if not was_valid and parts:
            reparee = True
        if not parts:
            meta.append(
                {
                    "libelle": props.get("libelle"),
                    "ug_id": props.get("ug_id"),
                    "type_geom": gtype,
                    "invalide": gtype in ("polygon", "multipolygon"),
                    "reparee": not was_valid,
                }
            )
            continue
        for p in parts:
            polys.append(p)
            meta.append(
                {
                    "libelle": props.get("libelle"),
                    "ug_id": props.get("ug_id"),
                    "type_geom": "polygon",
                    "reparee": not was_valid,
                }
            )
    return meta, polys, reparee


def reproject_polygons(polys: list[Polygon], srid: int) -> list[Polygon]:
    if srid == 4326:
        return polys
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{srid}", always_xy=True)

    def _xf(x: float, y: float, z: float | None = None):
        return transformer.transform(x, y)

    return [transform(_xf, p) for p in polys]


def geom_hash(polys: list[Polygon]) -> str:
    """Hash MD5 de la géométrie normalisée (WKB union)."""
    if not polys:
        return hashlib.md5(b"empty").hexdigest()
    u = unary_union(polys)
    # Normalize: make_valid + wkb
    u = make_valid(u)
    return hashlib.md5(u.wkb).hexdigest()


def surface_ha(polys: list[Polygon]) -> float:
    """Aire en ha. `polys` déjà dans un SCR métrique adapté (voir SRID_AIRE)."""
    if not polys:
        return 0.0
    u = unary_union(polys)
    return round(float(u.area) / 10_000.0, 4)


def _id_mesure(attributs: dict[str, Any]) -> int:
    raw = attributs.get("ID", 1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _write_shapefile(gdf: gpd.GeoDataFrame, shp_path: Path, srid: int) -> None:
    """Écrit .shp/.shx/.dbf/.prj avec les largeurs et types de DBF_SCHEMA."""
    import shapefile
    from pyproj import CRS
    from pyproj.enums import WktVersion
    from shapely.geometry import mapping

    stem = shp_path.with_suffix("")
    with shapefile.Writer(str(stem), shapeType=shapefile.POLYGON) as w:
        for field in DBF_SCHEMA:
            w.field(
                str(field["name"]),
                str(field["type"]),
                size=int(field["width"]),
                decimal=int(field.get("precision") or 0),
            )
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            w.shape(mapping(geom))
            values: list[Any] = []
            for field in DBF_SCHEMA:
                name = str(field["name"])
                val = row[name] if name in row.index else None
                if field["type"] == "N":
                    values.append(int(val) if val is not None and val != "" else 0)
                else:
                    values.append("" if val is None else str(val))
            w.record(*values)

    prj = stem.with_suffix(".prj")
    prj.write_text(CRS.from_epsg(srid).to_wkt(WktVersion.WKT1_ESRI), encoding="utf-8")


def build_geodataframe(
    polys_proj: list[Polygon],
    attributs: dict[str, str],
    strategie: str = STRATEGIE_GEOM_DEFAUT,
) -> gpd.GeoDataFrame:
    """Une mesure = mêmes attributs sur toutes les lignes, y compris ID (notice)."""
    id_val = _id_mesure(attributs)
    rows: list[dict[str, Any]] = []
    if strategie == "multipart":
        geom = MultiPolygon(polys_proj) if len(polys_proj) > 1 else polys_proj[0]
        rows.append({**attributs, "ID": id_val, "geometry": geom})
    else:
        for p in polys_proj:
            row = dict(attributs)
            row["ID"] = id_val
            row["geometry"] = p
            rows.append(row)
    gdf = gpd.GeoDataFrame(rows, geometry="geometry")
    if "ID" in gdf.columns:
        gdf["ID"] = gdf["ID"].astype("int64")
    for col in ("NOM", "CIBLE", "DESCRIPTIO", "DECISION", "REFEI", "CATEGORIE"):
        if col in gdf.columns:
            gdf[col] = gdf[col].astype(str)
    ordered = [c for c in DBF_FIELD_NAMES if c in gdf.columns]
    return gdf[ordered + ["geometry"]]


def write_zip(
    gdf: gpd.GeoDataFrame,
    *,
    srid: int,
    basename: str,
    out_dir: Path,
) -> Path:
    """Écrit le shapefile puis un ZIP à plat (.shp/.shx/.dbf à la racine).

    La notice Windows décrit la compression d'un dossier, mais la page d'import
    GéoMCE exige « au moins les fichiers en .shp, .shx et .dbf » dans le ZIP.
    Un niveau de dossier peut faire échouer l'import.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / basename
    if work.exists():
        import shutil

        shutil.rmtree(work)
    work.mkdir(parents=True)

    gdf = gdf.set_crs(epsg=srid, allow_override=True)
    shp_path = work / f"{basename}.shp"
    _write_shapefile(gdf, shp_path, srid)

    cpg = work / f"{basename}.cpg"
    cpg.write_text(ENCODING, encoding="ascii")

    zip_path = out_dir / f"{basename}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in work.iterdir():
            zf.write(f, arcname=f.name)
    return zip_path


def make_basename(projet_nom: str, mesure_nom: str, jour: date | None = None) -> str:
    jour = jour or date.today()
    stamp = jour.strftime("%Y%m%d")
    base = f"{slugify(projet_nom, 24)}_{slugify(mesure_nom, 24)}_{stamp}"
    return slugify(base, NOM_FICHIER_MAX)


def prepare_export_payload(
    projet: dict[str, Any],
    features: list[dict[str, Any]],
    *,
    mode: str = "complet",
    strategie: str = STRATEGIE_GEOM_DEFAUT,
) -> dict[str, Any]:
    """Prépare géométries + attributs pour aperçu / export (sans écrire)."""
    meta, polys_4326, _ = load_polygons_from_features(features)
    srid, srid_label = resolve_srid(projet.get("departement"))
    polys_proj: list[Polygon] = []
    if srid and polys_4326:
        polys_proj = reproject_polygons(polys_4326, srid)

    attrs, _ = build_attributs(
        nom=projet.get("geomce_nom"),
        cibles=projet.get("geomce_cible"),
        description=projet.get("geomce_description") or projet.get("description"),
        decision=projet.get("reference_decision"),
        refei=projet.get("reference_ei"),
        categorie=projet.get("geomce_categorie"),
        mode=mode,
    )

    ghash = geom_hash(polys_proj) if polys_proj else geom_hash(polys_4326)
    surf = None
    if polys_4326 and srid:
        srid_aire = SRID_AIRE.get(srid, srid)
        polys_aire = (
            polys_proj if srid_aire == srid and polys_proj else reproject_polygons(polys_4326, srid_aire)
        )
        surf = surface_ha(polys_aire)

    return {
        "meta_geoms": meta,
        "polys_proj": polys_proj,
        "srid": srid,
        "srid_label": srid_label,
        "attributs": attrs,
        "geom_hash": ghash,
        "surface_ha": surf,
        "nb_parties": len(polys_proj),
        "strategie": strategie,
        "mode": mode,
    }
