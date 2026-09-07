"""Export ZIP shapefile d'une UG (ou emprise, ou une géométrie)."""

from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from uuid import UUID

import geopandas as gpd
from shapely.geometry import shape

from .crud import lister_geometries_ug
from .ingestion import GeometryIngestError

_COUCHE_SUFFIX = {
    "surf": "surf",
    "lin": "lin",
    "pct": "pct",
    "emprise": "emprise",
}


def _slug(text: str, max_len: int = 40) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return (cleaned or "export")[:max_len]


def _dbf_name(raw: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", raw or "attr")[:10] or "attr"
    if base[0].isdigit():
        base = f"_{base[:9]}"
    name = base
    i = 2
    while name.lower() in used:
        suffix = str(i)
        name = f"{base[: 10 - len(suffix)]}{suffix}"
        i += 1
    used.add(name.lower())
    return name


def _scalar(val: Any) -> str | int | float | None:
    if val is None or isinstance(val, (int, float, str, bool)):
        if isinstance(val, bool):
            return "1" if val else "0"
        if isinstance(val, str):
            return val[:254]
        return val
    try:
        return json.dumps(val, ensure_ascii=False)[:254]
    except (TypeError, ValueError):
        return str(val)[:254]


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
    used: set[str] = set()
    row: dict[str, Any] = {"geometry": geom}
    for key, src in (
        ("id", feat.get("id") or props.get("id")),
        ("ug_id", props.get("ug_id")),
        ("libelle", props.get("libelle") or props.get("nom")),
        ("couche", props.get("couche")),
        ("src", props.get("source_fichier")),
    ):
        name = _dbf_name(key, used)
        if name.lower() in ("geometry", "geom"):
            name = _dbf_name("col", used)
        row[name] = _scalar(src)

    attributs = props.get("attributs")
    first = attributs[0] if isinstance(attributs, list) and attributs else None
    if isinstance(first, dict):
        for k, v in first.items():
            name = _dbf_name(str(k), used)
            if name.lower() in ("geometry", "geom"):
                name = _dbf_name("attr", used)
            row[name] = _scalar(v)
    return row


def _filtrer_features(
    fc: dict[str, Any],
    *,
    ug_id: str | None,
    emprise: bool,
    geom_id: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for feat in fc.get("features") or []:
        props = feat.get("properties") or {}
        couche = str(props.get("couche") or "")
        fid = str(feat.get("id") or props.get("id") or "")
        if geom_id:
            if fid != str(geom_id):
                continue
        elif emprise:
            if couche != "emprise" and str(props.get("ug_id") or "") != "emprise":
                continue
        else:
            if str(props.get("ug_id") or "") != str(ug_id or ""):
                continue
            if couche == "emprise":
                continue
        out.append(feat)
    return out


def exporter_shp_zip(
    projet_id: UUID,
    *,
    ug_id: str | None = None,
    emprise: bool = False,
    geom_id: str | None = None,
) -> tuple[bytes, str]:
    """Retourne (zip_bytes, filename). Lambert 93 (EPSG:2154)."""
    if not emprise and not (ug_id or "").strip() and not geom_id:
        raise GeometryIngestError("ug_id ou emprise requis.")

    fc = lister_geometries_ug(projet_id)
    features = _filtrer_features(fc, ug_id=ug_id, emprise=emprise, geom_id=geom_id)
    if not features:
        cible = "emprise" if emprise else (ug_id or geom_id or "cible")
        raise GeometryIngestError(f"Aucune géométrie à exporter ({cible}).")

    libelle = ""
    for feat in features:
        props = feat.get("properties") or {}
        libelle = str(props.get("libelle") or props.get("nom") or "")
        if libelle:
            break

    if emprise:
        basename = _slug(f"emprise_{libelle or 'projet'}")
    elif geom_id:
        basename = _slug(f"{libelle or ug_id or 'geom'}_{str(geom_id)[:8]}")
    else:
        basename = _slug(f"{libelle or ug_id or 'ug'}")

    par_couche: dict[str, list[dict[str, Any]]] = {}
    for feat in features:
        props = feat.get("properties") or {}
        couche = str(props.get("couche") or "surf")
        row = _row_from_feature(feat)
        if row is None:
            continue
        par_couche.setdefault(couche, []).append(row)

    if not par_couche:
        raise GeometryIngestError("Géométries vides, export impossible.")

    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="export_shp_") as tmp:
        tmp_path = Path(tmp)
        written = 0
        for couche, rows in par_couche.items():
            suffix = _COUCHE_SUFFIX.get(couche, couche)[:10]
            layer_name = basename if len(par_couche) == 1 else f"{basename}_{suffix}"
            gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
            try:
                gdf = gdf.to_crs(2154)
            except Exception as exc:
                raise GeometryIngestError(f"Reprojection Lambert 93 impossible: {exc}") from exc
            shp_path = tmp_path / f"{layer_name}.shp"
            try:
                gdf.to_file(shp_path, driver="ESRI Shapefile", encoding="utf-8")
            except Exception as exc:
                raise GeometryIngestError(f"Écriture shapefile impossible: {exc}") from exc
            cpg = tmp_path / f"{layer_name}.cpg"
            cpg.write_text("UTF-8", encoding="ascii")
            written += 1

        if written == 0:
            raise GeometryIngestError("Aucun fichier shapefile généré.")

        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(tmp_path.iterdir()):
                zf.write(path, arcname=path.name)

    return buf.getvalue(), f"{basename}.zip"
