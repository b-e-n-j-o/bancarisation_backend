"""Routes géométries projet : liste + ingestion shapefile ZIP."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from .crud import lister_geometries_ug
from .ingestion import GeometryIngestError, ingest_shapefile_zip

router = APIRouter()


@router.get("/projets/{projet_id}/geometries")
def list_geometries_route(projet_id: UUID) -> dict[str, Any]:
    try:
        return lister_geometries_ug(projet_id)
    except GeometryIngestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/projets/{projet_id}/geometries/ingest",
    status_code=status.HTTP_201_CREATED,
)
async def ingest_geometry_route(
    projet_id: UUID,
    file: UploadFile = File(...),
    ug_id: Optional[str] = Form(default=None),
    libelle: str = Form(default=""),
    description: str = Form(default=""),
    is_emprise: bool = Form(default=False),
) -> dict[str, Any]:
    try:
        content = await file.read()
        result = ingest_shapefile_zip(
            projet_id=projet_id,
            file_name=file.filename or "geometrie.zip",
            content=content,
            ug_id=ug_id,
            libelle=libelle,
            description=description,
            is_emprise=is_emprise,
        )
    except GeometryIngestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "id": result.id,
        "table": result.table,
        "couche": result.couche,
        "ug_id": result.ug_id,
        "libelle": result.libelle,
        "geometry_type": result.geometry_type,
        "nb_features_source": result.nb_features_source,
        "srid_source": result.srid_source,
    }
