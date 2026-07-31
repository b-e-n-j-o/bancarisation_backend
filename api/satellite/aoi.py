"""AOI : géométrie UG → bbox rectangulaire paddée (EPSG:3035 / 4326)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

from api.db.env import get_database_url

OUTPUT_EPSG = 3035
RESOLUTION_M = 10
MAX_DIMENSION_PX = 2500
PADDING_RATIO = 0.50
MIN_PADDING_M = 200.0

_SELECT_UG_GEOM = """
SELECT
    ug_id,
    %s AS couche,
    ST_AsGeoJSON(ST_Transform(geom_3857, 4326))::text AS geometry_geojson
FROM bancarisation.{table}
WHERE projet_id = %s AND ug_id = %s AND geom_3857 IS NOT NULL
ORDER BY created_at ASC
"""


class AoiError(Exception):
    pass


@dataclass(frozen=True)
class PaddedAoi:
    """Emprise rectangulaire paddée pour Sentinel Hub + affichage MapLibre."""

    geometry_4326: dict[str, Any]
    geometry_3035: dict[str, Any]
    bbox_4326: list[float]  # [west, south, east, north]
    bounds_3035: tuple[float, float, float, float]
    width_px: int
    height_px: int
    resolution_m: int
    epsg: int


def _load_ug_geometry_4326(projet_id: UUID, ug_id: str) -> dict[str, Any]:
    """Charge et unionne les géométries de l'UG (surf prioritaire, sinon lin/pct)."""
    features_by_couche: dict[str, list[dict[str, Any]]] = {
        "surf": [],
        "lin": [],
        "pct": [],
    }
    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                for table, couche in (
                    ("unites_de_gestion_surf", "surf"),
                    ("unites_de_gestion_lin", "lin"),
                    ("unites_de_gestion_pct", "pct"),
                ):
                    cur.execute(
                        _SELECT_UG_GEOM.format(table=table),
                        (couche, str(projet_id), ug_id),
                    )
                    for row in cur.fetchall():
                        geojson = row[2]
                        if not geojson:
                            continue
                        features_by_couche[couche].append(json.loads(geojson))
    except Exception as exc:
        raise AoiError(f"Lecture géométrie UG impossible: {exc}") from exc

    geoms: list[Any] = []
    for couche in ("surf", "lin", "pct"):
        if features_by_couche[couche]:
            geoms = [shape(g) for g in features_by_couche[couche]]
            break

    if not geoms:
        raise AoiError(f"Aucune géométrie pour l'UG « {ug_id} » sur ce projet.")

    merged = unary_union(geoms)
    if merged.is_empty:
        raise AoiError(f"Géométrie vide pour l'UG « {ug_id} ».")
    return mapping(merged)


def build_padded_aoi(projet_id: UUID, ug_id: str) -> PaddedAoi:
    """
    Bbox rectangulaire autour de l'UG :
    pad = max(side) * 25 %, minimum 100 m ; sortie Process API en EPSG:3035.
    """
    geom_4326 = _load_ug_geometry_4326(projet_id, ug_id)
    to_3035 = Transformer.from_crs("EPSG:4326", f"EPSG:{OUTPUT_EPSG}", always_xy=True)
    to_4326 = Transformer.from_crs(f"EPSG:{OUTPUT_EPSG}", "EPSG:4326", always_xy=True)

    geom_m = shapely_transform(to_3035.transform, shape(geom_4326))
    minx, miny, maxx, maxy = geom_m.bounds
    side_x = max(maxx - minx, 1.0)
    side_y = max(maxy - miny, 1.0)
    pad = max(max(side_x, side_y) * PADDING_RATIO, MIN_PADDING_M)  # 50 % / côté, min 200 m

    padded = box(minx - pad, miny - pad, maxx + pad, maxy + pad)
    pminx, pminy, pmaxx, pmaxy = padded.bounds

    width_px = max(1, round((pmaxx - pminx) / RESOLUTION_M))
    height_px = max(1, round((pmaxy - pminy) / RESOLUTION_M))
    if max(width_px, height_px) > MAX_DIMENSION_PX:
        raise AoiError(
            f"AOI trop grande : {width_px}x{height_px} px à {RESOLUTION_M} m "
            f"(max {MAX_DIMENSION_PX}). Réduire le padding ou découper l'UG."
        )

    padded_4326 = shapely_transform(to_4326.transform, padded)
    west, south, east, north = padded_4326.bounds

    return PaddedAoi(
        geometry_4326=mapping(padded_4326),
        geometry_3035=mapping(padded),
        bbox_4326=[west, south, east, north],
        bounds_3035=(pminx, pminy, pmaxx, pmaxy),
        width_px=width_px,
        height_px=height_px,
        resolution_m=RESOLUTION_M,
        epsg=OUTPUT_EPSG,
    )
