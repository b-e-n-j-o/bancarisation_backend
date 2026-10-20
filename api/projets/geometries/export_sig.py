"""Export SIG du projet : un GeoPackage Lambert 93 par couche, dans un ZIP.

Chaque couche cochée devient un fichier distinct, ouvrable dans QGIS.
Les géométries hors unités (autre fichier ingéré, emprise, notes) apparaissent
dès qu'elles existent en base.
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import Any
from uuid import UUID

import geopandas as gpd
from shapely.geometry import shape

from .crud import lister_geometries_ug
from .ingestion import GeometryIngestError

_GEOM_SURF = {"Polygon", "MultiPolygon"}
_GEOM_LIN = {"LineString", "MultiLineString"}


def _slug(text: str, max_len: int = 48) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return (cleaned or "couche")[:max_len]


def _scalar(val: Any) -> str | int | float | None:
    if val is None or isinstance(val, (int, float, str)):
        if isinstance(val, str):
            return val[:2000]
        return val
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, list):
        if val and all(isinstance(x, str) for x in val):
            return ", ".join(val)[:2000]
        try:
            return json.dumps(val, ensure_ascii=False)[:2000]
        except (TypeError, ValueError):
            return str(val)[:2000]
    if isinstance(val, dict):
        try:
            return json.dumps(val, ensure_ascii=False)[:2000]
        except (TypeError, ValueError):
            return str(val)[:2000]
    return str(val)[:2000]


def _row_from_feature(feat: dict[str, Any]) -> dict[str, Any] | None:
    geom_raw = feat.get("geometry")
    if not geom_raw:
        return None
    try:
        geom = shape(geom_raw)
    except (TypeError, ValueError):
        return None
    if geom.is_empty:
        return None
    props = feat.get("properties") or {}
    row: dict[str, Any] = {"geometry": geom}
    skip = {"attributs", "ugs"}
    for key, val in props.items():
        if key in skip or str(key).startswith("_"):
            continue
        row[str(key)[:60]] = _scalar(val)
    if feat.get("id") and "id" not in row:
        row["id"] = _scalar(feat.get("id"))
    attributs = props.get("attributs")
    first = attributs[0] if isinstance(attributs, list) and attributs else None
    if isinstance(first, dict):
        for k, v in first.items():
            col = str(k)[:60]
            if col in ("geometry", "geom") or col in row:
                continue
            row[col] = _scalar(v)
    ugs = props.get("ugs")
    if isinstance(ugs, list) and ugs:
        row["ugs"] = _scalar(ugs)
    return row


def _frames(features: list[dict[str, Any]]) -> list[tuple[str, gpd.GeoDataFrame]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "surfaces": [],
        "lineaires": [],
        "points": [],
    }
    for feat in features:
        row = _row_from_feature(feat)
        if row is None:
            continue
        kind = row["geometry"].geom_type
        if kind in _GEOM_SURF:
            buckets["surfaces"].append(row)
        elif kind in _GEOM_LIN:
            buckets["lineaires"].append(row)
        else:
            buckets["points"].append(row)
    frames: list[tuple[str, gpd.GeoDataFrame]] = []
    presents = [(nom, rows) for nom, rows in buckets.items() if rows]
    for nom, rows in presents:
        gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
        try:
            gdf = gdf.to_crs(2154)
        except Exception as exc:
            raise GeometryIngestError(f"Reprojection Lambert 93 impossible : {exc}") from exc
        layer = nom if len(presents) > 1 else "geometrie"
        frames.append((layer, gdf))
    return frames


def _ecrire_gpkg(path: Path, frames: list[tuple[str, gpd.GeoDataFrame]], nom: str) -> None:
    for i, (layer, gdf) in enumerate(frames):
        nom_couche = nom if layer == "geometrie" and len(frames) == 1 else f"{nom}_{layer}"
        gdf.to_file(
            path,
            layer=nom_couche[:63],
            driver="GPKG",
            mode="w" if i == 0 else "a",
        )


def _features_projet(projet_id: UUID) -> list[dict[str, Any]]:
    fc = lister_geometries_ug(projet_id)
    return list(fc.get("features") or [])


def _est_emprise(props: dict[str, Any]) -> bool:
    return str(props.get("couche") or "") == "emprise" or str(props.get("ug_id") or "") == "emprise"


def _features_cadastre(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        from api.cadastre.crud import lister_cadastre_projet

        fc = lister_cadastre_projet(projet_id)
    except Exception:
        return []
    return list(fc.get("features") or [])


def _features_foncier(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        from api.foncier.crud import _lire_features

        return list(_lire_features(projet_id) or [])
    except Exception:
        return []


def _features_notes(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        from api.carto.notes.crud import geojson

        fc = geojson(projet_id)
    except Exception:
        return []
    return list(fc.get("features") or [])


def _autres_groupes(features: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groupes: dict[str, list[dict[str, Any]]] = {}
    for feat in features:
        props = feat.get("properties") or {}
        if _est_emprise(props):
            continue
        if str(props.get("statut") or "ug") == "ug" and str(props.get("ug_id") or "").strip():
            continue
        source = str(props.get("source_fichier") or "").strip()
        statut = str(props.get("statut") or "autre").strip() or "autre"
        cle = f"fichier:{source}" if source else f"statut:{statut}"
        groupes.setdefault(cle, []).append(feat)
    return groupes


def _libelle_autre(cle: str, features: list[dict[str, Any]]) -> str:
    if cle.startswith("fichier:"):
        return Path(cle.split(":", 1)[1]).name or "Fichier SIG"
    props = (features[0].get("properties") or {}) if features else {}
    statut = str(props.get("statut") or "autre")
    labels = {
        "contexte": "Géométries de contexte",
        "non_affectee": "Géométries non affectées",
        "ecartee": "Géométries écartées",
    }
    return labels.get(statut, f"Géométries ({statut})")


def lister_couches_sig(projet_id: UUID) -> list[dict[str, Any]]:
    """Couches exportables, uniquement celles qui ont au moins une géométrie."""
    features = _features_projet(projet_id)
    couches: list[dict[str, Any]] = []

    par_ug: dict[str, list[dict[str, Any]]] = {}
    emprise: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties") or {}
        if _est_emprise(props):
            emprise.append(feat)
            continue
        if str(props.get("statut") or "ug") != "ug":
            continue
        ug_id = str(props.get("ug_id") or "").strip()
        if not ug_id:
            continue
        par_ug.setdefault(ug_id, []).append(feat)

    for ug_id in sorted(par_ug, key=lambda s: s.casefold()):
        feats = par_ug[ug_id]
        props = feats[0].get("properties") or {}
        libelle = str(props.get("libelle") or props.get("nom") or ug_id)
        couches.append({
            "id": f"ug:{ug_id}",
            "groupe": "Unités de gestion",
            "label": libelle if libelle != ug_id else ug_id,
            "detail": ug_id if libelle != ug_id else None,
            "nb": len(feats),
        })

    if emprise:
        couches.append({
            "id": "emprise",
            "groupe": "Projet",
            "label": "Emprise",
            "detail": None,
            "nb": len(emprise),
        })

    for cle, feats in sorted(_autres_groupes(features).items(), key=lambda kv: kv[0].casefold()):
        couches.append({
            "id": f"autre:{cle}",
            "groupe": "Autres géométries",
            "label": _libelle_autre(cle, feats),
            "detail": None,
            "nb": len(feats),
        })

    cadastre = _features_cadastre(projet_id)
    if cadastre:
        couches.append({
            "id": "cadastre",
            "groupe": "Foncier",
            "label": "Cadastre",
            "detail": None,
            "nb": len(cadastre),
        })

    foncier = _features_foncier(projet_id)
    if foncier:
        couches.append({
            "id": "foncier",
            "groupe": "Foncier",
            "label": "Foncier",
            "detail": None,
            "nb": len(foncier),
        })

    notes = _features_notes(projet_id)
    if notes:
        couches.append({
            "id": "notes",
            "groupe": "Terrain",
            "label": "Notes terrain",
            "detail": None,
            "nb": len(notes),
        })

    return couches


def _selectionner(
    projet_id: UUID,
    ids: set[str],
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """(dossier, nom_fichier, features) pour chaque couche demandée."""
    features = _features_projet(projet_id)
    par_ug: dict[str, list[dict[str, Any]]] = {}
    emprise: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties") or {}
        if _est_emprise(props):
            emprise.append(feat)
            continue
        if str(props.get("statut") or "ug") != "ug":
            continue
        ug_id = str(props.get("ug_id") or "").strip()
        if ug_id:
            par_ug.setdefault(ug_id, []).append(feat)
    autres = _autres_groupes(features)

    out: list[tuple[str, str, list[dict[str, Any]]]] = []
    for ug_id, feats in par_ug.items():
        if f"ug:{ug_id}" not in ids:
            continue
        props = feats[0].get("properties") or {}
        libelle = str(props.get("libelle") or ug_id)
        out.append(("unites_de_gestion", _slug(libelle if libelle else ug_id), feats))
    if "emprise" in ids and emprise:
        out.append(("emprise", "emprise", emprise))
    for cle, feats in autres.items():
        if f"autre:{cle}" not in ids:
            continue
        out.append(("autres", _slug(_libelle_autre(cle, feats)), feats))
    if "cadastre" in ids:
        feats = _features_cadastre(projet_id)
        if feats:
            out.append(("cadastre", "cadastre", feats))
    if "foncier" in ids:
        feats = _features_foncier(projet_id)
        if feats:
            out.append(("foncier", "foncier", feats))
    if "notes" in ids:
        feats = _features_notes(projet_id)
        if feats:
            out.append(("notes_terrain", "notes_terrain", feats))
    return out


def exporter_sig_zip(projet_id: UUID, couche_ids: list[str] | None) -> tuple[bytes, str]:
    """ZIP contenant un GeoPackage EPSG:2154 par couche sélectionnée."""
    disponibles = lister_couches_sig(projet_id)
    if not disponibles:
        raise GeometryIngestError("Aucune géométrie à exporter pour ce projet.")
    ids = {c["id"] for c in disponibles} if not couche_ids else {c for c in couche_ids if c}
    inconnues = ids - {c["id"] for c in disponibles}
    if inconnues:
        raise GeometryIngestError("Couche inconnue : " + ", ".join(sorted(inconnues)))
    selection = _selectionner(projet_id, ids)
    if not selection:
        raise GeometryIngestError("Aucune géométrie dans la sélection.")

    noms_pris: set[str] = set()
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="export_sig_") as tmp:
        tmp_path = Path(tmp)
        for dossier, nom, feats in selection:
            frames = _frames(feats)
            if not frames:
                continue
            base = nom
            i = 2
            while f"{dossier}/{base}.gpkg".lower() in noms_pris:
                base = f"{nom}_{i}"
                i += 1
            noms_pris.add(f"{dossier}/{base}.gpkg".lower())
            dest_dir = tmp_path / dossier
            dest_dir.mkdir(parents=True, exist_ok=True)
            _ecrire_gpkg(dest_dir / f"{base}.gpkg", frames, base)

        fichiers = [p for p in tmp_path.rglob("*.gpkg") if p.is_file()]
        if not fichiers:
            raise GeometryIngestError("Aucune géométrie exportable dans la sélection.")

        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(fichiers):
                arc = path.relative_to(tmp_path).as_posix()
                zf.write(path, arcname=f"sig/{arc}")

    return buf.getvalue(), "sig_projet.zip"
