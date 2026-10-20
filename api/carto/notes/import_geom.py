"""Lecture SHP / GPKG / GeoJSON → une géométrie GeoJSON 4326 pour une note.

Même lecture que l’ingestion SIG du projet : CRS lu dans le fichier
(.prj, GPKG, GeoJSON WGS84). Pas de Lambert 93 inventé si le CRS manque.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from api.projets.geometries.sig_io import (
    appliquer_crs,
    identifier_epsg,
    lire_gdf,
    sources_depuis_chemin,
)

_MAX_BYTES = 40 * 1024 * 1024
_MAX_FEATURES = 800
_ALLOWED_EXT = {
    ".zip",
    ".gpkg",
    ".geopackage",
    ".geojson",
    ".json",
    ".kml",
    ".shp",
    ".dbf",
    ".shx",
    ".prj",
    ".cpg",
    ".sbn",
    ".sbx",
}


class ImportGeomError(Exception):
    pass


def _safe_name(name: str) -> str:
    raw = Path(name).name
    if not raw or raw.startswith(".") or ".." in raw:
        raise ImportGeomError("Nom de fichier invalide.")
    return raw


def _merge_geoms(geoms: list[Any]) -> Any:
    from shapely.geometry import GeometryCollection
    from shapely.ops import unary_union

    parts = [g for g in geoms if g is not None and not g.is_empty]
    if not parts:
        raise ImportGeomError("Aucune géométrie valide dans le fichier.")
    if len(parts) == 1:
        return parts[0]
    try:
        merged = unary_union(parts)
        if merged is not None and not merged.is_empty:
            return merged
    except Exception:  # noqa: BLE001
        pass
    return GeometryCollection(parts)


def _force_2d(geom: Any) -> Any:
    try:
        return geom.force_2d()
    except Exception:  # noqa: BLE001
        return geom


def _pick_main(paths: list[Path]) -> Path:
    by_ext = {p.suffix.lower(): p for p in paths}
    for ext in (".zip", ".gpkg", ".geopackage", ".geojson", ".json", ".kml", ".shp"):
        if ext in by_ext:
            return by_ext[ext]
    return paths[0]


def geometry_from_files(files: list[tuple[str, bytes]]) -> dict[str, Any]:
    if not files:
        raise ImportGeomError("Aucun fichier reçu.")
    total = sum(len(b) for _, b in files)
    if total > _MAX_BYTES:
        raise ImportGeomError("Fichier trop volumineux (limite 40 Mo).")

    tmp = Path(tempfile.mkdtemp(prefix="ann_geom_"))
    try:
        saved: list[Path] = []
        for name, content in files:
            ext = Path(name).suffix.lower()
            if ext not in _ALLOWED_EXT:
                raise ImportGeomError(f"Extension non supportée : {ext or name}")
            dest = tmp / _safe_name(name)
            dest.write_bytes(content)
            saved.append(dest)

        main = _pick_main(saved)
        sources = sources_depuis_chemin(main)
        if not sources:
            raise ImportGeomError("Aucun fichier SIG lisible.")

        geoms: list[Any] = []
        nb = 0
        epsg_source: int | None = None
        for path, layer, _nom in sources:
            gdf = lire_gdf(path, layer)
            if gdf is None or gdf.empty:
                continue
            epsg, motif = identifier_epsg(path, gdf)
            if epsg is None:
                raise ImportGeomError(motif or "Système de coordonnées introuvable.")
            if epsg_source is None:
                epsg_source = epsg
            gdf = appliquer_crs(gdf, epsg)
            if epsg != 4326:
                gdf = gdf.to_crs(4326)
            for geom in gdf.geometry:
                if geom is None or geom.is_empty:
                    continue
                geoms.append(_force_2d(geom))
                nb += 1
                if nb > _MAX_FEATURES:
                    raise ImportGeomError("Trop d'entités (limite 800).")

        merged = _merge_geoms(geoms)
        from shapely.geometry import mapping

        geojson = mapping(merged)
        if not isinstance(geojson, dict) or not geojson.get("type"):
            raise ImportGeomError("Conversion GeoJSON impossible.")
        return {
            "geometry": geojson,
            "nb_features": nb,
            "geom_type": geojson.get("type"),
            "epsg_source": epsg_source,
            "source_geom_key": str(uuid4()),
            "filename": main.name,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
