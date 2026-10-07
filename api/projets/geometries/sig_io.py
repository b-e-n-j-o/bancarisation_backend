"""Lecture commune ZIP / shapefile / GPKG / GeoJSON.

Même règle partout (aperçu, profil, persistance) :
- shapefile : EPSG via .prj / métadonnée ;
- GPKG : EPSG embarqué, sinon refus (pas de guess) ;
- GeoJSON : CRS déclaré, sinon WGS84 (RFC 7946) si les coordonnées
  tiennent dans ±180/±90 ; sinon refus (souvent du Lambert mal exporté).
"""
from __future__ import annotations

import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd

from .ingestion import GeometryIngestError

ENCODAGES = (None, "latin1", "cp1252")
EXTS_SIG = {".shp", ".gpkg", ".geojson", ".json"}


def est_fichier_macos(name: str) -> bool:
    n = name.lower()
    return n.startswith(".") or n.startswith("._") or n in {"thumbs.db", "desktop.ini"}


def _nom_zip(info: zipfile.ZipInfo) -> str:
    nom = info.filename
    if not info.flag_bits & 0x800:
        try:
            nom = nom.encode("cp437").decode("utf-8")
        except UnicodeError:
            pass
    nom = unicodedata.normalize("NFC", nom)
    out = []
    for ch in nom:
        try:
            b = ch.encode("cp1252")
            out.append(b.decode("cp850") if 0x80 <= b[0] <= 0x9F else ch)
        except UnicodeError:
            out.append(ch)
    return "".join(out)


def extraire_zip(chemin: str | Path) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="sig_"))
    with zipfile.ZipFile(chemin) as z:
        for info in z.infolist():
            nom = _nom_zip(info)
            if info.is_dir() or "__MACOSX" in nom or Path(nom).name.startswith("._"):
                continue
            if nom.startswith("/") or ".." in Path(nom).parts:
                continue
            cible = dest / Path(nom).name
            cible.write_bytes(z.read(info))
    return dest


def lire_gdf(path: Path, layer: str | None = None) -> gpd.GeoDataFrame:
    last: Exception | None = None
    for enc in ENCODAGES:
        try:
            kwargs: dict[str, Any] = {}
            if layer:
                kwargs["layer"] = layer
            if enc:
                kwargs["encoding"] = enc
            return gpd.read_file(path, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last if last else GeometryIngestError(f"{path.name} : lecture impossible")


def noms_couches(path: Path) -> list[str]:
    try:
        import pyogrio

        rows = pyogrio.list_layers(path)
        return [str(r[0]) for r in rows]
    except Exception:
        try:
            import fiona

            return [str(n) for n in fiona.listlayers(path)]
        except Exception:
            return [path.stem]


def sources_depuis_chemin(chemin: str | Path) -> list[tuple[Path, str | None, str]]:
    """(fichier, layer ou None, nom de couche UI / profil)."""
    p = Path(chemin)
    ext = p.suffix.lower()
    fichiers: list[Path]
    if ext == ".zip":
        dossier = extraire_zip(p)
        fichiers = sorted(
            q for q in dossier.iterdir()
            if q.is_file() and not est_fichier_macos(q.name) and q.suffix.lower() in EXTS_SIG
        )
    elif ext in EXTS_SIG:
        fichiers = [p]
    else:
        return []

    out: list[tuple[Path, str | None, str]] = []
    for f in fichiers:
        e = f.suffix.lower()
        if e == ".gpkg":
            layers = noms_couches(f)
            for nom in layers:
                couche = nom if len(layers) == 1 else f"{f.stem}__{nom}"
                out.append((f, nom, couche))
        else:
            out.append((f, None, f.stem))
    return out


def _coords_wgs84(gdf: gpd.GeoDataFrame) -> bool:
    sample = next((g for g in gdf.geometry.values if g is not None and not g.is_empty), None)
    if sample is None:
        return False
    minx, miny, maxx, maxy = sample.bounds
    return abs(minx) <= 180 and abs(maxx) <= 180 and abs(miny) <= 90 and abs(maxy) <= 90


def identifier_epsg(path: Path, gdf: gpd.GeoDataFrame) -> tuple[int | None, str]:
    """Retourne (epsg, motif). motif vide si OK."""
    if gdf.crs is not None:
        try:
            code = gdf.crs.to_epsg()
            if code:
                return int(code), gdf.crs.name or f"EPSG:{code}"
        except Exception:
            pass
    prj = path.with_suffix(".prj")
    if prj.exists():
        try:
            from pyproj import CRS

            crs = CRS.from_wkt(prj.read_text(errors="ignore"))
            code = crs.to_epsg(min_confidence=25)
            if code:
                return int(code), crs.name
        except Exception:
            pass
    ext = path.suffix.lower()
    if ext in {".geojson", ".json"}:
        if gdf.empty or gdf.geometry.isna().all():
            return None, "GeoJSON vide"
        if _coords_wgs84(gdf):
            return 4326, "GeoJSON RFC 7946 (WGS84)"
        return None, (
            "GeoJSON hors WGS84 sans CRS — exportez en 4326 "
            "ou livrez un GPKG / shapefile avec système de coordonnées."
        )
    return None, "CRS non identifié"


def appliquer_crs(gdf: gpd.GeoDataFrame, epsg: int) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        return gdf.set_crs(epsg, allow_override=True)
    return gdf
