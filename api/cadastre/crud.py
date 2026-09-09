"""Lecture GeoJSON du cadastre projet pour la carto."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg

from api.db.env import get_database_url


class CadastreReadError(Exception):
    pass


def _props_dict(props: Any) -> dict[str, Any]:
    if isinstance(props, dict):
        return props
    if isinstance(props, str):
        try:
            parsed = json.loads(props)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def lister_parcelles_pour_ug(projet_id: UUID, ug_id: str) -> list[dict[str, Any]]:
    """Métadonnées des parcelles qui composent une UG (table de jonction)."""
    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT idu, section, numero, code_insee, nom_com,
                           contenance, surface_inter_m2
                    FROM bancarisation.cadastre_parcelle_ug
                    WHERE projet_id = %s AND ug_id = %s
                    ORDER BY section NULLS LAST, numero NULLS LAST, idu
                    """,
                    (str(projet_id), ug_id),
                )
                rows = []
                for idu, section, numero, code_insee, nom_com, contenance, surf in cur.fetchall():
                    rows.append({
                        "idu": idu,
                        "section": section,
                        "numero": numero,
                        "code_insee": code_insee,
                        "nom_com": nom_com,
                        "contenance": contenance,
                        "surface_inter_m2": float(surf) if surf is not None else None,
                        "label": f"{section or ''} {numero or ''}".strip() or idu,
                    })
                return rows
    except Exception as exc:
        detail = str(exc).lower()
        if "cadastre_parcelle_ug" in detail and (
            "does not exist" in detail or "undefinedtable" in detail.replace(" ", "")
        ):
            return []
        raise CadastreReadError(f"Lecture parcelles UG impossible: {exc}") from exc


def lister_cadastre_projet(
    projet_id: UUID,
    *,
    ug_id: str | None = None,
    croisement: bool = False,
) -> dict[str, Any]:
    """FeatureCollection 4326 des parcelles cadastrales (snapshot ingestion).

    Si ``ug_id`` est fourni : uniquement les parcelles liées via
    ``cadastre_parcelle_ug`` (composition spatiale). Fallback soft si table absente.

    Si ``croisement`` : toutes les parcelles qui intersectent au moins une UG
    (l’onglet Foncier), pas le buffer autour du site.
    """
    features: list[dict[str, Any]] = []
    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                if ug_id:
                    cur.execute(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'bancarisation'
                              AND table_name = 'cadastre_parcelle_ug'
                        )
                        """
                    )
                    has_link = bool(cur.fetchone()[0])
                    if has_link:
                        cur.execute(
                            """
                            SELECT DISTINCT ON (c.idu)
                                c.id::text,
                                %s AS ug_id,
                                c.idu,
                                c.section,
                                c.numero,
                                c.code_insee,
                                c.nom_com,
                                c.contenance,
                                c.properties,
                                ST_AsGeoJSON(
                                    COALESCE(c.geom, ST_Transform(c.geom_3857, 4326))
                                )::text
                            FROM bancarisation.cadastre_parcelle c
                            INNER JOIN bancarisation.cadastre_parcelle_ug link
                                ON link.projet_id = c.projet_id
                               AND link.idu = c.idu
                            WHERE c.projet_id = %s
                              AND link.ug_id = %s
                            ORDER BY c.idu,
                                     (c.ug_id = '__projet__') DESC,
                                     c.fetched_at DESC
                            """,
                            (ug_id, str(projet_id), ug_id),
                        )
                    else:
                        # Ancien comportement (buffer / snapshot)
                        cur.execute(
                            """
                            SELECT DISTINCT ON (idu)
                                id::text,
                                ug_id,
                                idu,
                                section,
                                numero,
                                code_insee,
                                nom_com,
                                contenance,
                                properties,
                                ST_AsGeoJSON(
                                    COALESCE(geom, ST_Transform(geom_3857, 4326))
                                )::text
                            FROM bancarisation.cadastre_parcelle
                            WHERE projet_id = %s
                              AND (ug_id = %s OR ug_id = '__projet__')
                            ORDER BY idu, (ug_id = %s) DESC, fetched_at DESC
                            """,
                            (str(projet_id), ug_id, ug_id),
                        )
                elif croisement:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (c.idu)
                            c.id::text,
                            (
                              SELECT string_agg(DISTINCT link2.ug_id, ', ' ORDER BY link2.ug_id)
                              FROM bancarisation.cadastre_parcelle_ug link2
                              WHERE link2.projet_id = c.projet_id AND link2.idu = c.idu
                            ) AS ug_id,
                            c.idu,
                            c.section,
                            c.numero,
                            c.code_insee,
                            c.nom_com,
                            c.contenance,
                            c.properties,
                            ST_AsGeoJSON(
                                COALESCE(c.geom, ST_Transform(c.geom_3857, 4326))
                            )::text
                        FROM bancarisation.cadastre_parcelle c
                        INNER JOIN bancarisation.cadastre_parcelle_ug link
                            ON link.projet_id = c.projet_id
                           AND link.idu = c.idu
                        WHERE c.projet_id = %s
                        ORDER BY c.idu, c.fetched_at DESC
                        """,
                        (str(projet_id),),
                    )
                else:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (idu)
                            id::text,
                            ug_id,
                            idu,
                            section,
                            numero,
                            code_insee,
                            nom_com,
                            contenance,
                            properties,
                            ST_AsGeoJSON(
                                COALESCE(geom, ST_Transform(geom_3857, 4326))
                            )::text
                        FROM bancarisation.cadastre_parcelle
                        WHERE projet_id = %s
                        ORDER BY idu, fetched_at DESC
                        """,
                        (str(projet_id),),
                    )
                for row in cur.fetchall():
                    (
                        fid,
                        row_ug,
                        idu,
                        section,
                        numero,
                        code_insee,
                        nom_com,
                        contenance,
                        props,
                        geom_json,
                    ) = row
                    if not geom_json:
                        continue
                    geom = json.loads(geom_json)
                    props_obj = _props_dict(props)
                    features.append(
                        {
                            "type": "Feature",
                            "id": fid,
                            "geometry": geom,
                            "properties": {
                                "id": fid,
                                "ug_id": row_ug,
                                "idu": idu,
                                "section": section,
                                "numero": numero,
                                "code_insee": code_insee,
                                "nom_com": nom_com,
                                "contenance": contenance,
                                "label": f"{section or ''} {numero or ''}".strip() or idu,
                                **{
                                    k: v
                                    for k, v in props_obj.items()
                                    if k
                                    not in {
                                        "idu",
                                        "section",
                                        "numero",
                                        "code_insee",
                                        "nom_com",
                                        "contenance",
                                    }
                                },
                            },
                        }
                    )
    except Exception as exc:
        raise CadastreReadError(f"Lecture cadastre impossible: {exc}") from exc

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "nb_parcelles": len(features),
            "ug_id": ug_id,
            "croisement": croisement,
        },
    }
