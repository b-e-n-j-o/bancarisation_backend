"""Génération ZIP shapefile conforme gabarit GéoMCE."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform, unary_union
from pyproj import Transformer

from api.geomce.constantes import (
    CHAMP_VIDE,
    ENCODING,
    NOM_FICHIER_MAX,
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


def surface_ha(polys: list[Polygon], srid: int) -> float:
    if not polys:
        return 0.0
    # Aire en m² si SCR projeté métrique ; Guyane 3857 ≈ mètres aussi
    u = unary_union(polys)
    return round(float(u.area) / 10_000.0, 4)


def build_geodataframe(
    polys_proj: list[Polygon],
    attributs: dict[str, str],
    strategie: str = STRATEGIE_GEOM_DEFAUT,
) -> gpd.GeoDataFrame:
    rows: list[dict[str, Any]] = []
    if strategie == "multipart":
        geom = MultiPolygon(polys_proj) if len(polys_proj) > 1 else polys_proj[0]
        rows.append({**attributs, "geometry": geom})
    else:
        for i, p in enumerate(polys_proj, start=1):
            row = dict(attributs)
            row["ID"] = str(i)
            row["geometry"] = p
            rows.append(row)
    gdf = gpd.GeoDataFrame(rows, geometry="geometry")
    # Types string for DBF
    for col in ("ID", "NOM", "CIBLE", "DESCRIPTIO", "DECISION", "REFEI", "CATEGORIE"):
        if col in gdf.columns:
            gdf[col] = gdf[col].astype(str)
    return gdf


def write_zip(
    gdf: gpd.GeoDataFrame,
    *,
    srid: int,
    basename: str,
    out_dir: Path,
) -> Path:
    """Écrit shapefile dans un sous-dossier puis ZIP (comme notice Windows)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / basename
    if work.exists():
        import shutil

        shutil.rmtree(work)
    work.mkdir(parents=True)

    gdf = gdf.set_crs(epsg=srid, allow_override=True)
    shp_path = work / f"{basename}.shp"
    # Fiona / geopandas — encoding UTF-8
    gdf.to_file(shp_path, driver="ESRI Shapefile", encoding=ENCODING)

    cpg = work / f"{basename}.cpg"
    cpg.write_text(ENCODING, encoding="ascii")

    zip_path = out_dir / f"{basename}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in work.iterdir():
            # Fichiers dans un dossier au sein du ZIP
            zf.write(f, arcname=f"{basename}/{f.name}")
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
    surf = surface_ha(polys_proj, srid or 2154) if polys_proj else None

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
