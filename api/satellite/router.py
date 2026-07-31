"""Router FastAPI — suivi satellite Sentinel-2 par UG."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from api.satellite.aoi import AoiError
from api.satellite.auth import SentinelAuthError
from api.satellite.crud import (
    SatelliteCrudError,
    download_preview,
    download_tif,
    enrich_capture_urls,
    ensure_capture,
    get_capture_by_id,
    list_available_scenes,
    list_captures,
)
from api.satellite.sentinel import SentinelServiceError

router = APIRouter(prefix="/projets/{projet_id}/satellite", tags=["satellite"])


class CaptureRequest(BaseModel):
    ug_id: str = Field(..., min_length=1)
    date: date


def _http_from_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, AoiError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, SentinelAuthError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, (SentinelServiceError, SatelliteCrudError)):
        msg = str(exc)
        code = status.HTTP_502_BAD_GATEWAY
        if "trop grande" in msg.lower():
            code = status.HTTP_400_BAD_REQUEST
        return HTTPException(code, detail=msg)
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/scenes")
def get_scenes(
    projet_id: UUID,
    ug_id: str = Query(..., min_length=1),
    start: date = Query(..., alias="from"),
    end: date = Query(..., alias="to"),
) -> dict[str, Any]:
    """Dates Sentinel-2 disponibles (Catalog) sur la bbox paddée de l'UG."""
    if end < start:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="'to' doit être ≥ 'from'."
        )
    try:
        scenes = list_available_scenes(projet_id, ug_id, start, end)
    except (AoiError, SentinelAuthError, SentinelServiceError, SatelliteCrudError) as exc:
        raise _http_from_exc(exc) from exc
    return {
        "projet_id": str(projet_id),
        "ug_id": ug_id,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "scenes": scenes,
    }


@router.get("/captures")
def get_captures(
    projet_id: UUID,
    ug_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Captures déjà persistées (cache) pour le projet / UG."""
    try:
        rows = list_captures(projet_id, ug_id=ug_id)
    except SatelliteCrudError as exc:
        raise _http_from_exc(exc) from exc
    return {
        "projet_id": str(projet_id),
        "ug_id": ug_id,
        "captures": [enrich_capture_urls(r, projet_id) for r in rows],
    }


@router.post("/captures")
def post_capture(projet_id: UUID, body: CaptureRequest, response: Response) -> dict[str, Any]:
    """
    Récupère (ou crée) une capture pour ug_id + date.
    Idempotent : pas de nouvel appel Process si déjà en cache ready.
    """
    try:
        capture, created = ensure_capture(projet_id, body.ug_id, body.date)
    except (AoiError, SentinelAuthError, SentinelServiceError, SatelliteCrudError) as exc:
        raise _http_from_exc(exc) from exc

    response.status_code = (
        status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )
    payload = enrich_capture_urls(capture, projet_id)
    payload["created"] = created
    return payload


@router.get("/captures/{capture_id}")
def get_capture_detail(projet_id: UUID, capture_id: UUID) -> dict[str, Any]:
    try:
        capture = get_capture_by_id(projet_id, capture_id)
    except SatelliteCrudError as exc:
        raise _http_from_exc(exc) from exc
    if not capture:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Capture introuvable.")
    return enrich_capture_urls(capture, projet_id)


@router.get("/captures/{capture_id}/preview")
def get_capture_preview(projet_id: UUID, capture_id: UUID) -> Response:
    try:
        capture = get_capture_by_id(projet_id, capture_id)
        if not capture:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Capture introuvable.")
        content, mime = download_preview(capture)
    except HTTPException:
        raise
    except SatelliteCrudError as exc:
        raise _http_from_exc(exc) from exc
    return Response(
        content=content,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/captures/{capture_id}/tif")
def get_capture_tif(projet_id: UUID, capture_id: UUID) -> Response:
    try:
        capture = get_capture_by_id(projet_id, capture_id)
        if not capture:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Capture introuvable.")
        content, mime = download_tif(capture)
    except HTTPException:
        raise
    except SatelliteCrudError as exc:
        raise _http_from_exc(exc) from exc
    filename = f"s2_l2a_{capture.get('acquisition_date')}_bands.tif"
    return Response(
        content=content,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=86400",
        },
    )
