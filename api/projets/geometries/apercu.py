"""Aperçu GeoJSON 4326 — uniquement les couches que la passe 1 saura profiler."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from shapely.geometry import mapping

from .ingestion import GeometryIngestError
from .sig_io import (
    appliquer_crs,
    est_fichier_macos,
    identifier_epsg,
    lire_gdf,
    sources_depuis_chemin,
)

MAX_FEATURES = 8_000


def _vers_4326(gdf):
    if gdf.empty or gdf.geometry.isna().all() or gdf.crs is None:
        return gdf
    try:
        epsg = int(gdf.crs.to_epsg() or 0)
    except Exception:
        epsg = 0
    if epsg and epsg != 4326:
        return gdf.to_crs(epsg=4326)
    return gdf


def _features_gdf(gdf, couche: str) -> list[dict[str, Any]]:
    gdf = _vers_4326(gdf)
    out: list[dict[str, Any]] = []
    for i, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props = {
            k: (None if v != v else v)
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


def _lire_couche(path: Path, layer: str | None, nom: str, avertissements: list[str]) -> list[dict[str, Any]]:
    try:
        gdf = lire_gdf(path, layer)
    except Exception as exc:  # noqa: BLE001
        avertissements.append(f"{nom} : lecture impossible ({exc})")
        return []
    epsg, motif = identifier_epsg(path, gdf)
    if not epsg:
        avertissements.append(f"{nom} : {motif} — non affiché et non analysé.")
        return []
    gdf = appliquer_crs(gdf, epsg)
    feats = _features_gdf(gdf, nom)
    if not feats:
        avertissements.append(f"{nom} : couche vide (aucune géométrie).")
    return feats


def apercu_sig(fichiers: list[tuple[str, bytes]]) -> dict[str, Any]:
    if not fichiers:
        raise GeometryIngestError("Aucun fichier SIG.")

    features: list[dict[str, Any]] = []
    couches: list[str] = []
    avertissements: list[str] = []
    sig_present = False

    with tempfile.TemporaryDirectory(prefix="sig_apercu_") as tmp:
        root = Path(tmp)
        for nom, brut in fichiers:
            if not brut:
                continue
            name = Path(nom).name
            if est_fichier_macos(name):
                continue
            dest = root / name
            dest.write_bytes(brut)
            ext = dest.suffix.lower()
            if ext == ".shp":
                # sidecars écrits séparément ; on lit via le ZIP ou on ignore le SHP nu
                # sauf s'il est seul avec .prj (hors contrat passe 1).
                sig_present = True
                continue
            if ext not in {".zip", ".gpkg", ".geojson", ".json"}:
                continue
            sig_present = True
            sources = sources_depuis_chemin(dest)
            if not sources:
                avertissements.append(f"{name} : aucune couche SIG lisible.")
                continue
            for path, layer, couche in sources:
                feats = _lire_couche(path, layer, couche, avertissements)
                if feats:
                    couches.append(couche)
                    features.extend(feats)

        # shapefile hors ZIP : bloquant s'il n'y a rien d'autre d'analysable
        shps_nus = [
            p for p in root.iterdir()
            if p.is_file() and p.suffix.lower() == ".shp" and not est_fichier_macos(p.name)
        ]
        if shps_nus and not couches:
            avertissements.append(
                "Shapefile hors archive ZIP : zippez-le avec .shx, .dbf et .prj "
                "pour qu'il soit pris par l'analyse."
            )
        elif shps_nus:
            avertissements.append(
                "Shapefile hors ZIP ignoré — seuls le ZIP, le GeoPackage et le GeoJSON sont lus."
            )

    if not sig_present:
        avertissements.append("Aucune couche SIG lisible dans le dépôt.")

    if len(features) > MAX_FEATURES:
        avertissements.append(
            f"Aperçu limité à {MAX_FEATURES} entités sur {len(features)}."
        )
        features = features[:MAX_FEATURES]

    analysable = bool(couches)
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "nb_features": len(features),
            "couches": sorted(set(couches)),
            "avertissements": avertissements,
            "sig_present": sig_present,
            "analysable": analysable,
            "bloquant": sig_present and not analysable,
        },
    }
