"""Annotations terrain — CRUD PostGIS (Point 4326)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_database_url


class AnnotationError(Exception):
    pass


_SELECT = """
    a.id::text,
    a.projet_id::text,
    a.ug_id,
    a.note,
    a.titre,
    a.auteur,
    a.created_at::text,
    a.updated_at::text,
    COALESCE(
      ARRAY(SELECT unnest(a.document_ids)::text),
      '{}'::text[]
    ) AS document_ids,
    ST_X(a.geom) AS lng,
    ST_Y(a.geom) AS lat
"""


def _row_to_dict(r: dict[str, Any]) -> dict[str, Any]:
    docs = r.get("document_ids") or []
    if isinstance(docs, list):
        docs = [str(d) for d in docs]
    return {
        "id": r["id"],
        "projet_id": r["projet_id"],
        "ug_id": r.get("ug_id"),
        "note": r.get("note") or "",
        "titre": r.get("titre"),
        "auteur": r.get("auteur"),
        "document_ids": docs,
        "lng": float(r["lng"]),
        "lat": float(r["lat"]),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }


def lister(
    projet_id: UUID,
    *,
    ug_id: str | None = None,
) -> list[dict[str, Any]]:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if ug_id:
                cur.execute(
                    f"""
                    SELECT {_SELECT}
                    FROM bancarisation.annotation_terrain a
                    WHERE a.projet_id = %s AND a.ug_id = %s
                    ORDER BY a.created_at DESC
                    """,
                    (str(projet_id), ug_id),
                )
            else:
                cur.execute(
                    f"""
                    SELECT {_SELECT}
                    FROM bancarisation.annotation_terrain a
                    WHERE a.projet_id = %s
                    ORDER BY a.created_at DESC
                    """,
                    (str(projet_id),),
                )
            return [_row_to_dict(dict(r)) for r in cur.fetchall()]


def geojson(projet_id: UUID, *, ug_id: str | None = None) -> dict[str, Any]:
    rows = lister(projet_id, ug_id=ug_id)
    features = [
        {
            "type": "Feature",
            "id": r["id"],
            "geometry": {
                "type": "Point",
                "coordinates": [r["lng"], r["lat"]],
            },
            "properties": {
                "id": r["id"],
                "ug_id": r.get("ug_id"),
                "note": r.get("note") or "",
                "titre": r.get("titre"),
                "auteur": r.get("auteur"),
                "document_ids": r.get("document_ids") or [],
                "created_at": r.get("created_at"),
                "nb_docs": len(r.get("document_ids") or []),
                "label": (r.get("titre") or (r.get("note") or "Note")[:40]),
            },
        }
        for r in rows
    ]
    return {"type": "FeatureCollection", "features": features}


def creer(
    projet_id: UUID,
    *,
    lng: float,
    lat: float,
    note: str = "",
    titre: str | None = None,
    ug_id: str | None = None,
    auteur: str | None = None,
    document_ids: list[UUID] | None = None,
) -> dict[str, Any]:
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise AnnotationError("Coordonnées hors bornes.")
    docs = [str(d) for d in (document_ids or [])]
    ug = (ug_id or "").strip() or None

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bancarisation.annotation_terrain
                    (projet_id, ug_id, note, titre, geom, document_ids, auteur)
                VALUES (
                    %s, %s, %s, %s,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    %s::uuid[],
                    %s
                )
                RETURNING id
                """,
                (
                    str(projet_id),
                    ug,
                    note or "",
                    (titre or "").strip() or None,
                    lng,
                    lat,
                    docs,
                    (auteur or "").strip() or None,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise AnnotationError("Insertion échouée.")
            ann_id = str(row["id"])
            conn.commit()

    return lire(UUID(ann_id))


def lire(annotation_id: UUID) -> dict[str, Any]:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SELECT}
                FROM bancarisation.annotation_terrain a
                WHERE a.id = %s
                """,
                (str(annotation_id),),
            )
            r = cur.fetchone()
            if not r:
                raise AnnotationError("Annotation introuvable.")
            return _row_to_dict(dict(r))


def modifier(
    annotation_id: UUID,
    *,
    note: str | None = None,
    titre: str | None = None,
    ug_id: str | None = None,
    auteur: str | None = None,
    document_ids: list[UUID] | None = None,
    lng: float | None = None,
    lat: float | None = None,
) -> dict[str, Any]:
    sets: list[str] = ["updated_at = now()"]
    params: list[Any] = []

    if note is not None:
        sets.append("note = %s")
        params.append(note)
    if titre is not None:
        sets.append("titre = %s")
        params.append(titre.strip() or None)
    if ug_id is not None:
        sets.append("ug_id = %s")
        params.append(ug_id.strip() or None)
    if auteur is not None:
        sets.append("auteur = %s")
        params.append(auteur.strip() or None)
    if document_ids is not None:
        sets.append("document_ids = %s::uuid[]")
        params.append([str(d) for d in document_ids])
    if lng is not None and lat is not None:
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise AnnotationError("Coordonnées hors bornes.")
        sets.append("geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326)")
        params.extend([lng, lat])

    if len(sets) <= 1:
        return lire(annotation_id)

    params.append(str(annotation_id))
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE bancarisation.annotation_terrain
                SET {", ".join(sets)}
                WHERE id = %s
                """,
                params,
            )
            if cur.rowcount == 0:
                raise AnnotationError("Annotation introuvable.")
            conn.commit()
    return lire(annotation_id)


def supprimer(annotation_id: UUID) -> None:
    with psycopg.connect(get_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM bancarisation.annotation_terrain WHERE id = %s",
                (str(annotation_id),),
            )
            if cur.rowcount == 0:
                raise AnnotationError("Annotation introuvable.")
            conn.commit()
