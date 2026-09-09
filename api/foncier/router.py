"""Routes API foncier (geojson + import cadastre)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from .crud import FoncierError, geojson_parcelles, importer_depuis_cadastre

router = APIRouter()


def _err(exc: FoncierError, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    detail = str(exc)
    if "introuvable" in detail.lower():
        code = status.HTTP_404_NOT_FOUND
    return HTTPException(status_code=code, detail=detail)


@router.get("/projets/{projet_id}/foncier/geojson")
def geojson_route(projet_id: UUID) -> dict[str, Any]:
    """GeoJSON des parcelles foncières (coloration propriétaire)."""
    try:
        return geojson_parcelles(projet_id)
    except FoncierError as exc:
        raise _err(exc) from exc


@router.post("/projets/{projet_id}/foncier/parcelles/importer-cadastre")
def importer_cadastre_route(
    projet_id: UUID,
    idu: str | None = Query(default=None, description="Un IDU ; absent = toutes les parcelles du snapshot"),
) -> dict[str, Any]:
    """Charge les parcelles cadastrales qui intersectent les UG, puis les titres."""
    try:
        return importer_depuis_cadastre(projet_id, idu=idu.strip() if idu else None)
    except FoncierError as exc:
        raise _err(exc) from exc
