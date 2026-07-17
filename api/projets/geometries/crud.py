"""Lecture des géométries UG / emprise pour affichage carto (GeoJSON 4326)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg

from api.db.env import get_database_url

from .ingestion import GeometryIngestError

_SELECT_UG = """
SELECT
    id::text AS id,
    projet_id::text AS projet_id,
    ug_id,
    libelle,
    description,
    properties,
    source_fichier,
    %s AS couche,
    ST_AsGeoJSON(ST_Transform(geom_3857, 4326))::text AS geometry_geojson
FROM bancarisation.{table}
WHERE projet_id = %s
ORDER BY created_at ASC
"""

_SELECT_EMPRISE = """
SELECT
    id::text AS id,
    projet_id::text AS projet_id,
    NULL::text AS ug_id,
    libelle,
    description,
    properties,
    source_fichier,
    'emprise' AS couche,
    ST_AsGeoJSON(ST_Transform(geom_3857, 4326))::text AS geometry_geojson
FROM bancarisation.emprise_projet
WHERE projet_id = %s
ORDER BY created_at ASC
"""


def _row_to_feature(row: dict[str, Any]) -> dict[str, Any]:
    geom = json.loads(row["geometry_geojson"]) if row.get("geometry_geojson") else None
    props_raw = row.get("properties") or {}
    if isinstance(props_raw, str):
        try:
            props_raw = json.loads(props_raw)
        except json.JSONDecodeError:
            props_raw = {}

    return {
        "type": "Feature",
        "id": row["id"],
        "geometry": geom,
        "properties": {
            "id": row["id"],
            "projet_id": row["projet_id"],
            "ug_id": row.get("ug_id"),
            "nom": row.get("libelle") or row.get("ug_id") or "Sans nom",
            "libelle": row.get("libelle") or "",
            "description": row.get("description") or "",
            "couche": row.get("couche"),
            "source_fichier": row.get("source_fichier"),
            **({} if not isinstance(props_raw, dict) else {
                k: v for k, v in props_raw.items() if not str(k).startswith("_")
            }),
        },
    }


def lister_geometries_ug(projet_id: UUID) -> dict[str, Any]:
    """Retourne un FeatureCollection + métadonnées UG pour la carto projet."""
    features: list[dict[str, Any]] = []
    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                for table, couche in (
                    ("unites_de_gestion_surf", "surf"),
                    ("unites_de_gestion_lin", "lin"),
                    ("unites_de_gestion_pct", "pct"),
                ):
                    cur.execute(
                        _SELECT_UG.format(table=table),
                        (couche, str(projet_id)),
                    )
                    cols = [d.name for d in cur.description]
                    for tup in cur.fetchall():
                        features.append(_row_to_feature(dict(zip(cols, tup))))

                cur.execute(_SELECT_EMPRISE, (str(projet_id),))
                cols = [d.name for d in cur.description]
                for tup in cur.fetchall():
                    features.append(_row_to_feature(dict(zip(cols, tup))))
    except Exception as exc:
        raise GeometryIngestError(f"Lecture géométries impossible: {exc}") from exc

    ugs_map: dict[str, dict[str, Any]] = {}
    for f in features:
        props = f.get("properties") or {}
        if props.get("couche") == "emprise":
            continue
        ug_id = props.get("ug_id")
        if not ug_id:
            continue
        if ug_id not in ugs_map:
            ugs_map[ug_id] = {
                "id": ug_id,
                "libelle": props.get("libelle") or ug_id,
                "description": props.get("description") or "",
                "couches": [],
            }
        couche = props.get("couche")
        if couche and couche not in ugs_map[ug_id]["couches"]:
            ugs_map[ug_id]["couches"].append(couche)

    return {
        "type": "FeatureCollection",
        "features": features,
        "ugs": list(ugs_map.values()),
        "meta": {
            "nb_features": len(features),
            "nb_ugs": len(ugs_map),
            "nb_emprise": sum(
                1 for f in features if (f.get("properties") or {}).get("couche") == "emprise"
            ),
        },
    }
