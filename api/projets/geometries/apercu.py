"""Aperçu GeoJSON 4326 d'un dépôt SIG (ZIP shapefile, GPKG, GeoJSON)."""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import mapping

from .ingestion import GeometryIngestError

MAX_FEATURES = 8_000
_SIG_EXTS = {".shp", ".gpkg", ".geojson", ".json"}
# QGIS écrit souvent UTF-8 dans le .cpg alors que le DBF est encore latin-1
# (noms de champs tronqués avec octets 0xE9/0xEF…). On retente alors.
_ENCODAGES = (None, "latin1", "cp1252")


def _est_fichier_macos(name: str) -> bool:
    n = name.lower()
    return n.startswith(".") or n.startswith("._") or n in {"thumbs.db", "desktop.ini"}


def lire_gdf(path: Path, layer: str | None = None) -> gpd.GeoDataFrame:
    """Lit une source SIG en retentant l'encodage si le .cpg ment."""
    last: Exception | None = None
    for enc in _ENCODAGES:
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


def _noms_couches(path: Path) -> list[str]:
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


def _vers_4326(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.empty or gdf.geometry.isna().all():
        return gdf
    if gdf.crs is None:
        sample = next((g for g in gdf.geometry.values if g is not None and not g.is_empty), None)
        if sample is not None:
            minx, miny, maxx, maxy = sample.bounds
            if abs(minx) > 180 or abs(maxx) > 180 or abs(miny) > 90 or abs(maxy) > 90:
                gdf = gdf.set_crs(2154, allow_override=True)
            else:
                gdf = gdf.set_crs(4326, allow_override=True)
        else:
            return gdf
    try:
        epsg = int(gdf.crs.to_epsg() or 0)
    except Exception:
        epsg = 0
    if epsg != 4326:
        gdf = gdf.to_crs(epsg=4326)
    return gdf


def _features_gdf(gdf: gpd.GeoDataFrame, couche: str) -> list[dict[str, Any]]:
    gdf = _vers_4326(gdf)
    out: list[dict[str, Any]] = []
    for i, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props = {
            k: (None if v != v else v)  # NaN
            for k, v in row.drop(labels=["geometry"], errors="ignore").items()
            if v is not None
        }
        props["couche"] = couche
        try:
            gj = mapping(geom)
        except Exception:
            continue
        out.append({
            "type": "Feature",
            "id": f"{couche}-{i}",
            "properties": {str(k): (v if isinstance(v, (str, int, float, bool)) or v is None else str(v))
                           for k, v in props.items()},
            "geometry": gj,
        })
    return out


def _lire_source(path: Path, couche_defaut: str) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for nom in _noms_couches(path):
        try:
            gdf = lire_gdf(path, layer=nom) if nom else lire_gdf(path)
        except Exception:
            try:
                gdf = lire_gdf(path)
            except Exception as exc:
                raise GeometryIngestError(f"{path.name} : {exc}") from exc
        features.extend(_features_gdf(gdf, nom or couche_defaut))
    return features


def _extraire_zip(dest: Path, root: Path, avertissements: list[str]) -> None:
    try:
        with zipfile.ZipFile(dest) as zf:
            for info in zf.infolist():
                name = info.filename
                if name.startswith("/") or ".." in Path(name).parts:
                    continue
                if _est_fichier_macos(Path(name).name) or "__MACOSX" in name:
                    continue
                cible = root / Path(name).name
                cible.write_bytes(zf.read(info))
    except zipfile.BadZipFile as exc:
        avertissements.append(f"{dest.name} : ZIP invalide ({exc})")


def apercu_sig(fichiers: list[tuple[str, bytes]]) -> dict[str, Any]:
    if not fichiers:
        raise GeometryIngestError("Aucun fichier SIG.")

    features: list[dict[str, Any]] = []
    couches: list[str] = []
    avertissements: list[str] = []

    with tempfile.TemporaryDirectory(prefix="sig_apercu_") as tmp:
        root = Path(tmp)
        zips: list[Path] = []
        # 1. Écrire tous les fichiers (shp + sidecars .dbf/.prj/…) avant toute lecture.
        for nom, brut in fichiers:
            if not brut:
                continue
            name = Path(nom).name
            if _est_fichier_macos(name):
                continue
            dest = root / name
            dest.write_bytes(brut)
            if dest.suffix.lower() == ".zip":
                zips.append(dest)

        for zpath in zips:
            _extraire_zip(zpath, root, avertissements)

        vus: dict[str, Path] = {}
        for p in root.iterdir():
            if not p.is_file() or _est_fichier_macos(p.name):
                continue
            if p.suffix.lower() not in _SIG_EXTS:
                continue
            vus.setdefault(p.stem.lower(), p)
        sources = sorted(vus.values(), key=lambda p: p.name.lower())
        if not sources:
            avertissements.append("Aucune couche SIG lisible dans le dépôt.")

        for src in sources:
            try:
                feats = _lire_source(src, src.stem)
            except GeometryIngestError as exc:
                avertissements.append(str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                avertissements.append(f"{src.name} : {exc}")
                continue
            if feats:
                couches.append(src.stem)
                features.extend(feats)
            else:
                avertissements.append(f"{src.stem} : couche vide (aucune géométrie).")

    if len(features) > MAX_FEATURES:
        avertissements.append(
            f"Aperçu limité à {MAX_FEATURES} entités sur {len(features)}."
        )
        features = features[:MAX_FEATURES]

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "nb_features": len(features),
            "couches": sorted(set(couches)),
            "avertissements": avertissements,
        },
    }
