"""Routes API foncier — lecture du jeu persisté à l'ingestion."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from .crud import FoncierError, geojson_parcelles

router = APIRouter()


def _err(exc: FoncierError, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    detail = str(exc)
    if "introuvable" in detail.lower():
        code = status.HTTP_404_NOT_FOUND
    return HTTPException(status_code=code, detail=detail)


@router.get("/projets/{projet_id}/foncier/geojson")
def geojson_route(projet_id: UUID) -> dict[str, Any]:
    """GeoJSON des parcelles foncières persistées (UG ∩ cadastre IGN)."""
    try:
        return geojson_parcelles(projet_id)
    except FoncierError as exc:
        raise _err(exc) from exc
