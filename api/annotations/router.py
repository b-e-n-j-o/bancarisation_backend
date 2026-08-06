"""Routes annotations terrain (notes / photos géolocalisées)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from . import crud

router = APIRouter()


class AnnotationCreate(BaseModel):
    lng: float = Field(..., ge=-180, le=180)
    lat: float = Field(..., ge=-90, le=90)
    note: str = ""
    titre: str | None = None
    ug_id: str | None = None
    auteur: str | None = None
    document_ids: list[UUID] = Field(default_factory=list)


class AnnotationUpdate(BaseModel):
    note: str | None = None
    titre: str | None = None
    ug_id: str | None = None
    auteur: str | None = None
    document_ids: list[UUID] | None = None
    lng: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)


def _err(exc: crud.AnnotationError, code: int = status.HTTP_400_BAD_REQUEST):
    if "introuvable" in str(exc).lower():
        code = status.HTTP_404_NOT_FOUND
    return HTTPException(status_code=code, detail=str(exc))


@router.get("/projets/{projet_id}/annotations")
def lister_route(
    projet_id: UUID,
    ug_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    try:
        return crud.lister(projet_id, ug_id=ug_id)
    except crud.AnnotationError as exc:
        raise _err(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Annotations indisponibles : {exc}",
        ) from exc


@router.get("/projets/{projet_id}/annotations.geojson")
def geojson_route(
    projet_id: UUID,
    ug_id: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return crud.geojson(projet_id, ug_id=ug_id)
    except crud.AnnotationError as exc:
        raise _err(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Annotations indisponibles : {exc}",
        ) from exc


@router.post(
    "/projets/{projet_id}/annotations",
    status_code=status.HTTP_201_CREATED,
)
def creer_route(projet_id: UUID, payload: AnnotationCreate) -> dict[str, Any]:
    try:
        return crud.creer(
            projet_id,
            lng=payload.lng,
            lat=payload.lat,
            note=payload.note,
            titre=payload.titre,
            ug_id=payload.ug_id,
            auteur=payload.auteur,
            document_ids=payload.document_ids,
        )
    except crud.AnnotationError as exc:
        raise _err(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/annotations/{annotation_id}")
def lire_route(annotation_id: UUID) -> dict[str, Any]:
    try:
        return crud.lire(annotation_id)
    except crud.AnnotationError as exc:
        raise _err(exc) from exc


@router.patch("/annotations/{annotation_id}")
def modifier_route(annotation_id: UUID, payload: AnnotationUpdate) -> dict[str, Any]:
    try:
        return crud.modifier(
            annotation_id,
            note=payload.note,
            titre=payload.titre,
            ug_id=payload.ug_id,
            auteur=payload.auteur,
            document_ids=payload.document_ids,
            lng=payload.lng,
            lat=payload.lat,
        )
    except crud.AnnotationError as exc:
        raise _err(exc) from exc


@router.delete("/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_route(annotation_id: UUID) -> Response:
    try:
        crud.supprimer(annotation_id)
    except crud.AnnotationError as exc:
        raise _err(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
