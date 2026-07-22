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


_SELECT_PARC_UG = """
SELECT
    ug.id::text AS feature_id,
    ug.projet_id::text AS projet_id,
    ug.ug_id,
    ug.libelle,
    ug.description,
    ug.source_fichier,
    %s AS couche,
    p.nom AS projet_nom,
    p.departement,
    p.commune,
    ST_AsGeoJSON(ST_Transform(ug.geom_3857, 4326))::text AS geometry_geojson
FROM bancarisation.{table} ug
JOIN bancarisation.projets p ON p.id = ug.projet_id
WHERE ug.geom_3857 IS NOT NULL
  AND (%s::text IS NULL OR p.departement = %s)
ORDER BY p.nom, ug.ug_id, ug.created_at
"""


def lister_geometries_parc(*, departement: str | None = None) -> dict[str, Any]:
    """FeatureCollection de toutes les UG du parc (liste projets / carte nationale).

    Géométrie = ``geom_3857`` (transformée en 4326 pour MapLibre), pas le jsonb
    ``properties``. ``properties.id`` = ``projet_id`` pour le zoom liste ↔ carte.
    """
    features: list[dict[str, Any]] = []
    dept = departement.strip() if departement and departement.strip() else None
    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                for table, couche in (
                    ("unites_de_gestion_surf", "surf"),
                    ("unites_de_gestion_lin", "lin"),
                    ("unites_de_gestion_pct", "pct"),
                ):
                    cur.execute(
                        _SELECT_PARC_UG.format(table=table),
                        (couche, dept, dept),
                    )
                    cols = [d.name for d in cur.description]
                    for tup in cur.fetchall():
                        row = dict(zip(cols, tup))
                        geom = (
                            json.loads(row["geometry_geojson"])
                            if row.get("geometry_geojson")
                            else None
                        )
                        if not geom:
                            continue
                        features.append(
                            {
                                "type": "Feature",
                                "id": row["feature_id"],
                                "geometry": geom,
                                "properties": {
                                    "id": row["projet_id"],
                                    "feature_id": row["feature_id"],
                                    "projet_id": row["projet_id"],
                                    "source": "utilisateur",
                                    "ug_id": row.get("ug_id"),
                                    "nom": row.get("projet_nom") or "Sans nom",
                                    "libelle": row.get("libelle") or "",
                                    "description": row.get("description") or "",
                                    "couche": row.get("couche"),
                                    "table_type": row.get("couche"),
                                    "departement": row.get("departement"),
                                    "commune": row.get("commune"),
                                    "source_fichier": row.get("source_fichier"),
                                },
                            }
                        )
    except Exception as exc:
        raise GeometryIngestError(f"Lecture géométries parc impossible: {exc}") from exc

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "nb_features": len(features),
            "nb_projets": len({f["properties"]["projet_id"] for f in features}),
            "departement": dept,
        },
    }


def compter_projets_parc_par_departement() -> list[dict[str, Any]]:
    """Décompte des projets du parc ayant au moins une UG, par département."""
    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      p.departement,
                      count(DISTINCT p.id)::int AS nb_projets
                    FROM bancarisation.projets p
                    WHERE p.departement IS NOT NULL
                      AND p.departement <> ''
                      AND (
                        EXISTS (
                          SELECT 1 FROM bancarisation.unites_de_gestion_surf u
                          WHERE u.projet_id = p.id AND u.geom_3857 IS NOT NULL
                        )
                        OR EXISTS (
                          SELECT 1 FROM bancarisation.unites_de_gestion_lin u
                          WHERE u.projet_id = p.id AND u.geom_3857 IS NOT NULL
                        )
                        OR EXISTS (
                          SELECT 1 FROM bancarisation.unites_de_gestion_pct u
                          WHERE u.projet_id = p.id AND u.geom_3857 IS NOT NULL
                        )
                      )
                    GROUP BY p.departement
                    ORDER BY nb_projets DESC, p.departement
                    """
                )
                return [
                    {"departement": str(r[0]), "nb_projets": int(r[1])}
                    for r in cur.fetchall()
                ]
    except Exception as exc:
        raise GeometryIngestError(f"Stats départements parc impossibles: {exc}") from exc
