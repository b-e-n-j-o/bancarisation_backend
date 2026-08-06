"""Export parcellaire : intersections géométriques UG ∩ parcelles cadastrales."""

from __future__ import annotations

import csv
import io
import json
from typing import Any
from uuid import UUID

import psycopg

from api.db.env import get_database_url

from .crud import CadastreReadError


def exporter_parcellaire_intersections(
    projet_id: UUID,
    *,
    ug_ids: list[str] | None = None,
) -> dict[str, Any]:
    """GeoJSON 4326 des **morceaux** de parcelles couverts par les UG.

    Chaque feature = une intersection UG ∩ parcelle (tronquée si l'UG
    n'occupe qu'une partie de la parcelle).
    """
    pid = str(projet_id)
    features: list[dict[str, Any]] = []
    # NULL = toutes les UG ; sinon liste filtrée
    ug_filter = ug_ids if ug_ids else None

    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH ugs AS (
                        SELECT
                            ug_id,
                            MAX(NULLIF(TRIM(libelle), '')) AS libelle,
                            ST_Union(geom_3857) AS geom_3857
                        FROM (
                            SELECT ug_id, libelle, geom_3857
                            FROM bancarisation.unites_de_gestion_surf
                            WHERE projet_id = %s
                              AND geom_3857 IS NOT NULL
                              AND ug_id IS NOT NULL AND ug_id <> ''
                              AND (%s::text[] IS NULL OR ug_id = ANY(%s))
                            UNION ALL
                            SELECT ug_id, libelle, geom_3857
                            FROM bancarisation.unites_de_gestion_lin
                            WHERE projet_id = %s
                              AND geom_3857 IS NOT NULL
                              AND ug_id IS NOT NULL AND ug_id <> ''
                              AND (%s::text[] IS NULL OR ug_id = ANY(%s))
                            UNION ALL
                            SELECT ug_id, libelle, geom_3857
                            FROM bancarisation.unites_de_gestion_pct
                            WHERE projet_id = %s
                              AND geom_3857 IS NOT NULL
                              AND ug_id IS NOT NULL AND ug_id <> ''
                              AND (%s::text[] IS NULL OR ug_id = ANY(%s))
                        ) t
                        GROUP BY ug_id
                    ),
                    parcels AS (
                        SELECT DISTINCT ON (idu)
                            idu,
                            section,
                            numero,
                            code_insee,
                            nom_com,
                            contenance,
                            geom_3857
                        FROM bancarisation.cadastre_parcelle
                        WHERE projet_id = %s
                          AND geom_3857 IS NOT NULL
                        ORDER BY idu,
                                 (ug_id = '__projet__') DESC,
                                 fetched_at DESC
                    ),
                    inter AS (
                        SELECT
                            u.ug_id,
                            COALESCE(u.libelle, u.ug_id) AS libelle_ug,
                            p.idu,
                            p.section,
                            p.numero,
                            p.code_insee,
                            p.nom_com,
                            p.contenance,
                            ST_MakeValid(
                                ST_Intersection(
                                    ST_MakeValid(u.geom_3857),
                                    ST_MakeValid(p.geom_3857)
                                )
                            ) AS geom_inter,
                            ST_Area(ST_MakeValid(p.geom_3857)) AS surface_parcelle_m2
                        FROM ugs u
                        JOIN parcels p
                          ON ST_Intersects(
                              ST_MakeValid(u.geom_3857),
                              ST_MakeValid(p.geom_3857)
                          )
                    )
                    SELECT
                        ug_id,
                        libelle_ug,
                        idu,
                        section,
                        numero,
                        code_insee,
                        nom_com,
                        contenance,
                        surface_parcelle_m2,
                        NULLIF(ST_Area(ST_CollectionExtract(geom_inter, 3)), 0)
                            AS surface_inter_m2,
                        CASE
                            WHEN ST_Dimension(geom_inter) >= 2
                             AND surface_parcelle_m2 > 0
                             AND COALESCE(
                                 ST_Area(ST_CollectionExtract(geom_inter, 3)), 0
                             ) < (surface_parcelle_m2 * 0.995)
                            THEN true
                            WHEN ST_Dimension(geom_inter) < 2
                            THEN true
                            ELSE false
                        END AS tronquee,
                        ST_AsGeoJSON(ST_Transform(geom_inter, 4326))::text AS geom_json,
                        ST_AsText(ST_Transform(geom_inter, 4326)) AS wkt_4326
                    FROM inter
                    WHERE geom_inter IS NOT NULL
                      AND NOT ST_IsEmpty(geom_inter)
                      AND (
                        ST_Dimension(geom_inter) < 2
                        OR COALESCE(ST_Area(ST_CollectionExtract(geom_inter, 3)), 0) > 0.5
                      )
                    ORDER BY ug_id, section NULLS LAST, numero NULLS LAST, idu
                    """,
                    (
                        pid, ug_filter, ug_filter,
                        pid, ug_filter, ug_filter,
                        pid, ug_filter, ug_filter,
                        pid,
                    ),
                )

                for row in cur.fetchall():
                    (
                        ug_id,
                        libelle_ug,
                        idu,
                        section,
                        numero,
                        code_insee,
                        nom_com,
                        contenance,
                        surf_parc,
                        surf_inter,
                        tronquee,
                        geom_json,
                        wkt,
                    ) = row
                    if not geom_json:
                        continue
                    geom = json.loads(geom_json)
                    label = f"{section or ''} {numero or ''}".strip() or idu
                    features.append(
                        {
                            "type": "Feature",
                            "id": f"{ug_id}:{idu}",
                            "geometry": geom,
                            "properties": {
                                "ug_id": ug_id,
                                "libelle_ug": libelle_ug,
                                "idu": idu,
                                "section": section,
                                "numero": numero,
                                "code_insee": code_insee,
                                "nom_com": nom_com,
                                "contenance": contenance,
                                "label": label,
                                "surface_parcelle_m2": (
                                    round(float(surf_parc), 2) if surf_parc is not None else None
                                ),
                                "surface_inter_m2": (
                                    round(float(surf_inter), 2) if surf_inter is not None else None
                                ),
                                "tronquee": bool(tronquee),
                                "wkt_4326": wkt,
                            },
                        }
                    )
    except CadastreReadError:
        raise
    except Exception as exc:
        detail = str(exc).lower()
        if "does not exist" in detail or "undefined" in detail:
            raise CadastreReadError(
                "Tables cadastre / UG absentes — appliquer les migrations 007/008."
            ) from exc
        raise CadastreReadError(f"Export parcellaire impossible: {exc}") from exc

    ug_set = sorted({str(f["properties"]["ug_id"]) for f in features})
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "projet_id": pid,
            "nb_features": len(features),
            "nb_ugs": len(ug_set),
            "ug_ids": ug_set,
            "description": (
                "Géométries = intersection UG ∩ parcelle (tronquées si l'UG "
                "n'occupe qu'une partie de la parcelle)."
            ),
        },
    }


def feature_collection_to_csv(fc: dict[str, Any]) -> str:
    """CSV métier : une ligne par intersection, avec WKT du morceau."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "ug_id",
            "libelle_ug",
            "idu",
            "section",
            "numero",
            "code_insee",
            "nom_com",
            "contenance_m2",
            "surface_parcelle_m2",
            "surface_inter_m2",
            "tronquee",
            "wkt_4326",
        ]
    )
    for feat in fc.get("features") or []:
        p = feat.get("properties") or {}
        writer.writerow(
            [
                p.get("ug_id"),
                p.get("libelle_ug"),
                p.get("idu"),
                p.get("section"),
                p.get("numero"),
                p.get("code_insee"),
                p.get("nom_com"),
                p.get("contenance"),
                p.get("surface_parcelle_m2"),
                p.get("surface_inter_m2"),
                "oui" if p.get("tronquee") else "non",
                p.get("wkt_4326"),
            ]
        )
    return buf.getvalue()
