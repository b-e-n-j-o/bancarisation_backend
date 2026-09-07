"""Routes annotations terrain (notes / photos géolocalisées)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, model_validator

from . import crud

router = APIRouter()


class AnnotationCreate(BaseModel):
    """Géométrie GeoJSON, ou lng/lat pour un point (rétrocompat)."""

    geometry: dict[str, Any] | None = None
    lng: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)
    note: str = ""
    titre: str | None = None
    ug_id: str | None = None
    auteur: str | None = None
    document_ids: list[UUID] = Field(default_factory=list)
    source_kind: str = "draw"
    source_geom_key: str | None = None
    source_annotation_id: UUID | None = None
    source_ug_geom_id: str | None = None
    observed_at: str | None = None

    @model_validator(mode="after")
    def _require_geom(self) -> AnnotationCreate:
        if self.geometry is None and (self.lng is None or self.lat is None):
            raise ValueError("Fournir geometry (GeoJSON) ou lng/lat.")
        return self

    def resolved_geometry(self) -> dict[str, Any]:
        if self.geometry:
            return self.geometry
        assert self.lng is not None and self.lat is not None
        return {"type": "Point", "coordinates": [self.lng, self.lat]}


class AnnotationUpdate(BaseModel):
    note: str | None = None
    titre: str | None = None
    ug_id: str | None = None
    auteur: str | None = None
    document_ids: list[UUID] | None = None
    geometry: dict[str, Any] | None = None
    lng: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)
    observed_at: str | None = None

    def resolved_geometry(self) -> dict[str, Any] | None:
        if self.geometry:
            return self.geometry
        if self.lng is not None and self.lat is not None:
            return {"type": "Point", "coordinates": [self.lng, self.lat]}
        return None


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
            geometry=payload.resolved_geometry(),
            note=payload.note,
            titre=payload.titre,
            ug_id=payload.ug_id,
            auteur=payload.auteur,
            document_ids=payload.document_ids,
            source_kind=payload.source_kind,
            source_geom_key=payload.source_geom_key,
            source_annotation_id=payload.source_annotation_id,
            source_ug_geom_id=payload.source_ug_geom_id,
            observed_at=payload.observed_at,
        )
    except crud.AnnotationError as exc:
        raise _err(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/projets/{projet_id}/annotations/import-geom")
async def import_geom_route(
    projet_id: UUID,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    """Importe SHP (ZIP ou sidecars), GeoPackage, GeoJSON → géométrie 4326."""
    del projet_id  # réservé au scoping URL / droits futurs
    from .import_geom import ImportGeomError, geometry_from_files

    payloads: list[tuple[str, bytes]] = []
    for up in files:
        name = up.filename or "fichier"
        data = await up.read()
        payloads.append((name, data))
    try:
        return geometry_from_files(payloads)
    except ImportGeomError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Import impossible : {exc}",
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
            geometry=payload.resolved_geometry(),
            observed_at=payload.observed_at,
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
