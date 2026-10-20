"""HTTP — analyse structurée d'un arrêté déjà déposé."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from .jobs import relancer, vue_document

router = APIRouter(prefix="/ocr/arx", tags=["arx"])


@router.get("/documents/{document_id}")
def lire_analyse(document_id: UUID) -> dict:
    vue = vue_document(str(document_id))
    if vue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document introuvable.")
    return vue


@router.post("/documents/{document_id}/analyser", status_code=status.HTTP_202_ACCEPTED)
def lancer_analyse(document_id: UUID) -> dict:
    try:
        return relancer(str(document_id))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
