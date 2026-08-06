"""Persistance des parcelles cadastrales autour des UG."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from pyproj import Transformer

from api.db.env import get_database_url

from .ign_client import CadastreIgnError
_TO_3857 = Transformer.from_crs(4326, 3857, always_xy=True).transform


def _as_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    raise CadastreIgnError(f"Parcelle non polygonale: {geom.geom_type}")


def remplacer_parcelles_ug(
    *,
    projet_id: UUID,
    ug_id: str,
    features: list[dict[str, Any]],
    buffer_m: float,
) -> int:
    """Remplace le snapshot cadastre d'une UG. Retourne le nombre de parcelles écrites."""
    with psycopg.connect(get_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM bancarisation.cadastre_parcelle
                WHERE projet_id = %s AND ug_id = %s
                """,
                (str(projet_id), ug_id),
            )
            n = 0
            for feat in features:
                props = feat.get("properties") or {}
                idu = str(props.get("idu") or "").strip()
                if not idu:
                    continue
                geom_raw = feat.get("geometry")
                if not geom_raw:
                    continue
                try:
                    geom_4326 = _as_multipolygon(shape(geom_raw))
                    geom_3857 = _as_multipolygon(transform(_TO_3857, geom_4326))
                except Exception:  # noqa: BLE001
                    continue

                cur.execute(
                    """
                    INSERT INTO bancarisation.cadastre_parcelle (
                        projet_id, ug_id, idu, section, numero, code_insee, nom_com,
                        contenance, geom, geom_3857, properties, buffer_m
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        ST_SetSRID(ST_GeomFromText(%s), 4326),
                        ST_SetSRID(ST_GeomFromText(%s), 3857),
                        %s::jsonb, %s
                    )
                    ON CONFLICT (projet_id, ug_id, idu) DO UPDATE SET
                        section = EXCLUDED.section,
                        numero = EXCLUDED.numero,
                        code_insee = EXCLUDED.code_insee,
                        nom_com = EXCLUDED.nom_com,
                        contenance = EXCLUDED.contenance,
                        geom = EXCLUDED.geom,
                        geom_3857 = EXCLUDED.geom_3857,
                        properties = EXCLUDED.properties,
                        buffer_m = EXCLUDED.buffer_m,
                        fetched_at = now()
                    """,
                    (
                        str(projet_id),
                        ug_id,
                        idu,
                        props.get("section"),
                        props.get("numero"),
                        props.get("code_insee"),
                        props.get("nom_com"),
                        props.get("contenance"),
                        geom_4326.wkt,
                        geom_3857.wkt,
                        json.dumps(props, default=str),
                        buffer_m,
                    ),
                )
                n += 1
        conn.commit()
    return n


def supprimer_cadastre_projet(projet_id: UUID, *, ug_ids: list[str] | None = None) -> None:
    with psycopg.connect(get_database_url()) as conn:
        with conn.cursor() as cur:
            if ug_ids:
                cur.execute(
                    """
                    DELETE FROM bancarisation.cadastre_parcelle
                    WHERE projet_id = %s AND ug_id = ANY(%s)
                    """,
                    (str(projet_id), ug_ids),
                )
            else:
                cur.execute(
                    "DELETE FROM bancarisation.cadastre_parcelle WHERE projet_id = %s",
                    (str(projet_id),),
                )
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'bancarisation'
                      AND table_name = 'cadastre_parcelle_ug'
                )
                """
            )
            if cur.fetchone()[0]:
                if ug_ids:
                    cur.execute(
                        """
                        DELETE FROM bancarisation.cadastre_parcelle_ug
                        WHERE projet_id = %s AND ug_id = ANY(%s)
                        """,
                        (str(projet_id), ug_ids),
                    )
                else:
                    cur.execute(
                        "DELETE FROM bancarisation.cadastre_parcelle_ug WHERE projet_id = %s",
                        (str(projet_id),),
                    )
        conn.commit()


def lier_parcelles_aux_ugs(projet_id: UUID) -> dict[str, int]:
    """Croisement spatial : parcelles du snapshot qui intersectent chaque UG.

    Remplit ``cadastre_parcelle_ug``. Retourne ``{ug_id: nb_parcelles}``.
    """
    pid = str(projet_id)
    counts: dict[str, int] = {}
    with psycopg.connect(get_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM bancarisation.cadastre_parcelle_ug WHERE projet_id = %s",
                (pid,),
            )
            cur.execute(
                """
                WITH parcels AS (
                    SELECT DISTINCT ON (idu)
                        projet_id,
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
                ugs AS (
                    SELECT ug_id, ST_Union(geom_3857) AS geom_3857
                    FROM (
                        SELECT ug_id, geom_3857
                        FROM bancarisation.unites_de_gestion_surf
                        WHERE projet_id = %s AND geom_3857 IS NOT NULL
                          AND ug_id IS NOT NULL AND ug_id <> ''
                        UNION ALL
                        SELECT ug_id, geom_3857
                        FROM bancarisation.unites_de_gestion_lin
                        WHERE projet_id = %s AND geom_3857 IS NOT NULL
                          AND ug_id IS NOT NULL AND ug_id <> ''
                        UNION ALL
                        SELECT ug_id, geom_3857
                        FROM bancarisation.unites_de_gestion_pct
                        WHERE projet_id = %s AND geom_3857 IS NOT NULL
                          AND ug_id IS NOT NULL AND ug_id <> ''
                    ) t
                    GROUP BY ug_id
                )
                INSERT INTO bancarisation.cadastre_parcelle_ug (
                    projet_id, ug_id, idu, section, numero, code_insee, nom_com,
                    contenance, surface_inter_m2
                )
                SELECT
                    p.projet_id,
                    u.ug_id,
                    p.idu,
                    p.section,
                    p.numero,
                    p.code_insee,
                    p.nom_com,
                    p.contenance,
                    NULL::numeric
                FROM parcels p
                JOIN ugs u ON ST_Intersects(p.geom_3857, u.geom_3857)
                ON CONFLICT (projet_id, ug_id, idu) DO UPDATE SET
                    section = EXCLUDED.section,
                    numero = EXCLUDED.numero,
                    code_insee = EXCLUDED.code_insee,
                    nom_com = EXCLUDED.nom_com,
                    contenance = EXCLUDED.contenance
                """,
                (pid, pid, pid, pid),
            )
            cur.execute(
                """
                SELECT ug_id, COUNT(*)::int
                FROM bancarisation.cadastre_parcelle_ug
                WHERE projet_id = %s
                GROUP BY ug_id
                """,
                (pid,),
            )
            for ug_id, n in cur.fetchall():
                counts[str(ug_id)] = int(n)
        conn.commit()
    return counts
