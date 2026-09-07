"""Export ZIP shapefile des entités CAO déjà calées (EPSG:2154)."""

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
from psycopg.errors import UndefinedTable
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from api.carto.persist import PlanCaoError, _connect, _verifier_projet

_MSG_NON_CALE = "Caler le plan avant d’exporter (Lambert-93)."


def _slug(text: str, max_len: int = 40) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return (cleaned or "plan_cao")[:max_len]


def _bucket(geom: BaseGeometry) -> str | None:
    t = geom.geom_type
    if t in ("Point", "MultiPoint"):
        return "pct"
    if t in ("LineString", "MultiLineString", "LinearRing"):
        return "lin"
    if t in ("Polygon", "MultiPolygon"):
        return "pol"
    return None


def _eclater(geom: BaseGeometry) -> list[BaseGeometry]:
    if geom.is_empty:
        return []
    if geom.geom_type == "GeometryCollection":
        out: list[BaseGeometry] = []
        for part in geom.geoms:
            out.extend(_eclater(part))
        return out
    if geom.geom_type.startswith("Multi"):
        return [p for p in geom.geoms if not p.is_empty]
    return [geom]


def _row(geom: BaseGeometry, attrs: dict[str, Any]) -> dict[str, Any]:
    return {
        "geometry": geom,
        "id": attrs["id"],
        "calque": (attrs.get("calque") or "")[:254],
        "dxf_type": (attrs.get("dxf_type") or "")[:254],
        "handle": (attrs.get("handle") or "")[:254],
        "texte": (attrs.get("texte") or "")[:254],
    }


def exporter_shp_zip(projet_id: UUID, plan_id: UUID) -> tuple[bytes, str]:
    """Retourne (zip_bytes, filename). Un SHP par type (points / lignes / polygones)."""
    with _connect() as conn:
        _verifier_projet(conn, projet_id)
        try:
            plan = conn.execute(
                """
                SELECT id::text, nom_fichier, calage_mode
                FROM bancarisation.plan_cao
                WHERE id = %s AND projet_id = %s
                """,
                (str(plan_id), str(projet_id)),
            ).fetchone()
        except UndefinedTable as exc:
            raise PlanCaoError(
                "Tables plan_cao absentes — appliquer backend/api/ocr/db/sql/035_plan_cao.sql"
            ) from exc
        if not plan:
            raise PlanCaoError("Plan CAO introuvable.")
        if plan["calage_mode"] == "non_cale":
            raise PlanCaoError(_MSG_NON_CALE)

        entites = conn.execute(
            """
            SELECT id, calque, dxf_type, handle, texte,
                   ST_AsGeoJSON(geom) AS geom
            FROM bancarisation.plan_cao_entite
            WHERE plan_id = %s
              AND geom IS NOT NULL
              AND NOT ST_IsEmpty(geom)
            """,
            (str(plan_id),),
        ).fetchall()

    par_couche: dict[str, list[dict[str, Any]]] = {"pct": [], "lin": [], "pol": []}
    for e in entites:
        raw = json.loads(e["geom"]) if e["geom"] else None
        if not raw:
            continue
        try:
            geom = shape(raw)
        except (TypeError, ValueError):
            continue
        attrs = {
            "id": int(e["id"]),
            "calque": e["calque"],
            "dxf_type": e["dxf_type"],
            "handle": e["handle"],
            "texte": e["texte"],
        }
        for part in _eclater(geom):
            bucket = _bucket(part)
            if not bucket:
                continue
            par_couche[bucket].append(_row(part, attrs))

    nonempty = {k: v for k, v in par_couche.items() if v}
    if not nonempty:
        raise PlanCaoError("Aucune géométrie calée à exporter.")

    stem = _slug(Path(plan["nom_fichier"] or "plan_cao").stem)
    basename = f"{stem}_l93"

    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="cao_shp_") as tmp:
        tmp_path = Path(tmp)
        written = 0
        for suffix, rows in nonempty.items():
            layer = basename if len(nonempty) == 1 else f"{basename}_{suffix}"
            gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:2154")
            shp_path = tmp_path / f"{layer}.shp"
            try:
                gdf.to_file(shp_path, driver="ESRI Shapefile", encoding="utf-8")
            except Exception as exc:
                raise PlanCaoError(f"Écriture shapefile impossible : {exc}") from exc
            (tmp_path / f"{layer}.cpg").write_text("UTF-8", encoding="ascii")
            written += 1

        if written == 0:
            raise PlanCaoError("Aucun fichier shapefile généré.")

        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(tmp_path.iterdir()):
                zf.write(path, arcname=path.name)

    return buf.getvalue(), f"{basename}.zip"
