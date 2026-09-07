"""Annotations terrain — CRUD PostGIS (géométrie 4326)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_database_url

ALLOWED_GEOM_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}

_MAX_COORDS = 20_000


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
    ST_AsGeoJSON(a.geom)::text AS geometry_geojson,
    ST_X(ST_PointOnSurface(a.geom)) AS lng,
    ST_Y(ST_PointOnSurface(a.geom)) AS lat,
    GeometryType(a.geom) AS geom_type,
    a.source_kind,
    a.source_geom_key,
    a.source_annotation_id::text,
    a.source_ug_geom_id,
    a.observed_at::text
"""


def _walk_coords(node: Any, acc: list[tuple[float, float]]) -> None:
    if not isinstance(node, list) or not node:
        return
    if isinstance(node[0], (int, float)):
        if len(node) < 2:
            raise AnnotationError("Coordonnée incomplète.")
        lng = float(node[0])
        lat = float(node[1])
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise AnnotationError("Coordonnées hors bornes.")
        acc.append((lng, lat))
        return
    for child in node:
        _walk_coords(child, acc)
        if len(acc) > _MAX_COORDS:
            raise AnnotationError("Géométrie trop dense (limite 20 000 sommets).")


def _close_ring(ring: list[Any]) -> list[Any]:
    if not isinstance(ring, list) or len(ring) < 3:
        raise AnnotationError("Anneau de polygone trop court.")
    first = ring[0]
    last = ring[-1]
    if first != last:
        ring = [*ring, first]
    if len(ring) < 4:
        raise AnnotationError("Anneau de polygone trop court.")
    return ring


def _normalize_geometry(geom: dict[str, Any]) -> dict[str, Any]:
    gtype = geom.get("type")
    if gtype not in ALLOWED_GEOM_TYPES:
        raise AnnotationError(f"Type de géométrie non supporté : {gtype}.")

    if gtype == "GeometryCollection":
        geos = geom.get("geometries")
        if not isinstance(geos, list) or not geos:
            raise AnnotationError("GeometryCollection vide.")
        return {
            "type": "GeometryCollection",
            "geometries": [_normalize_geometry(g) for g in geos if isinstance(g, dict)],
        }

    coords = geom.get("coordinates")
    if not isinstance(coords, list) or not coords:
        raise AnnotationError("Géométrie sans coordonnées.")

    if gtype == "Polygon":
        coords = [_close_ring(list(ring)) for ring in coords]
    elif gtype == "MultiPolygon":
        coords = [[_close_ring(list(ring)) for ring in poly] for poly in coords]

    acc: list[tuple[float, float]] = []
    _walk_coords(coords, acc)
    if not acc:
        raise AnnotationError("Géométrie sans sommet valide.")

    if gtype == "LineString" and len(acc) < 2:
        raise AnnotationError("Une ligne nécessite au moins 2 sommets.")
    if gtype == "Polygon" and len(acc) < 4:
        raise AnnotationError("Un polygone nécessite au moins 3 sommets distincts.")

    return {"type": gtype, "coordinates": coords}


def _row_to_dict(r: dict[str, Any]) -> dict[str, Any]:
    docs = r.get("document_ids") or []
    if isinstance(docs, list):
        docs = [str(d) for d in docs]
    raw = r.get("geometry_geojson")
    geometry: dict[str, Any] | None = None
    if isinstance(raw, dict):
        geometry = raw
    elif isinstance(raw, str) and raw:
        geometry = json.loads(raw)
    lng = r.get("lng")
    lat = r.get("lat")
    if geometry is None and lng is not None and lat is not None:
        geometry = {"type": "Point", "coordinates": [float(lng), float(lat)]}
    return {
        "id": r["id"],
        "projet_id": r["projet_id"],
        "ug_id": r.get("ug_id"),
        "note": r.get("note") or "",
        "titre": r.get("titre"),
        "auteur": r.get("auteur"),
        "document_ids": docs,
        "geometry": geometry,
        "geom_type": (r.get("geom_type") or (geometry or {}).get("type") or "Point"),
        "lng": float(lng) if lng is not None else None,
        "lat": float(lat) if lat is not None else None,
        "source_kind": r.get("source_kind") or "draw",
        "source_geom_key": r.get("source_geom_key") or r["id"],
        "source_annotation_id": r.get("source_annotation_id"),
        "source_ug_geom_id": r.get("source_ug_geom_id"),
        "observed_at": r.get("observed_at"),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }


def _feature_from_row(r: dict[str, Any]) -> dict[str, Any]:
    geom = r.get("geometry")
    if not geom and r.get("lng") is not None and r.get("lat") is not None:
        geom = {"type": "Point", "coordinates": [r["lng"], r["lat"]]}
    return {
        "type": "Feature",
        "id": r["id"],
        "geometry": geom,
        "properties": {
            "id": r["id"],
            "ug_id": r.get("ug_id"),
            "note": r.get("note") or "",
            "titre": r.get("titre"),
            "auteur": r.get("auteur"),
            "document_ids": r.get("document_ids") or [],
            "created_at": r.get("created_at"),
            "nb_docs": len(r.get("document_ids") or []),
            "geom_type": r.get("geom_type"),
            "source_kind": r.get("source_kind"),
            "source_geom_key": r.get("source_geom_key"),
            "observed_at": r.get("observed_at"),
            "label": (r.get("titre") or (r.get("note") or "Note")[:40]),
        },
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
    return {
        "type": "FeatureCollection",
        "features": [_feature_from_row(r) for r in rows if r.get("geometry")],
    }


def creer(
    projet_id: UUID,
    *,
    geometry: dict[str, Any],
    note: str = "",
    titre: str | None = None,
    ug_id: str | None = None,
    auteur: str | None = None,
    document_ids: list[UUID] | None = None,
    source_kind: str = "draw",
    source_geom_key: str | None = None,
    source_annotation_id: UUID | None = None,
    source_ug_geom_id: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    geom = _normalize_geometry(geometry)
    docs = [str(d) for d in (document_ids or [])]
    ug = (ug_id or "").strip() or None
    kind = (source_kind or "draw").strip() or "draw"
    if kind not in ("draw", "import", "ug", "annotation"):
        raise AnnotationError("source_kind invalide.")
    key = (source_geom_key or "").strip() or str(uuid4())
    src_ann = str(source_annotation_id) if source_annotation_id else None
    src_ug = (source_ug_geom_id or "").strip() or None
    obs = (observed_at or "").strip() or None

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bancarisation.annotation_terrain
                    (projet_id, ug_id, note, titre, geom, document_ids, auteur,
                     source_kind, source_geom_key, source_annotation_id,
                     source_ug_geom_id, observed_at)
                VALUES (
                    %s, %s, %s, %s,
                    ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                    %s::uuid[],
                    %s, %s, %s, %s, %s,
                    COALESCE(%s::date, CURRENT_DATE)
                )
                RETURNING id
                """,
                (
                    str(projet_id),
                    ug,
                    note or "",
                    (titre or "").strip() or None,
                    json.dumps(geom),
                    docs,
                    (auteur or "").strip() or None,
                    kind,
                    key,
                    src_ann,
                    src_ug,
                    obs,
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
    geometry: dict[str, Any] | None = None,
    observed_at: str | None = None,
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
    if geometry is not None:
        geom = _normalize_geometry(geometry)
        sets.append("geom = ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)")
        params.append(json.dumps(geom))
    if observed_at is not None:
        sets.append("observed_at = COALESCE(%s::date, observed_at)")
        params.append(observed_at.strip() or None)

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
