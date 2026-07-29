"""Routes OCR / extraction d'arrêtés préfectoraux."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from api.controle import crud
from api.controle.schemas import ArreteOut
from api.documents.crud_document import DocumentServiceError, get_document_content
from api.ocr.arrete import service as arrete_service
from api.parc.deps import MembreContext, get_membre_context

logger = logging.getLogger("arrete.router")

router = APIRouter(prefix="/ocr/arrete", tags=["ocr-arrete"])


@router.post("/extraction")
async def extraction_fichier(
    file: UploadFile = File(..., description="PDF d'arrêté à analyser (sans persistance)"),
) -> dict:
    """
    OCR + extraction LLM sur un PDF — renvoie le JSON structuré sans écrire en base.
    Utile pour tester le prompt / le modèle.
    """
    filename = (file.filename or "arrete.pdf").strip() or "arrete.pdf"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Fichier vide")
    lower = filename.lower()
    ctype = (file.content_type or "").lower()
    if not (lower.endswith(".pdf") or "pdf" in ctype):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seul un PDF est accepté",
        )
    try:
        return arrete_service.extraire_fichier_seul(content, filename)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Extraction fichier KO")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Extraction impossible : {exc}",
        ) from exc


@router.post(
    "/projets/{projet_id}/arretes/{arrete_id}/extraire",
    response_model=ArreteOut,
)
async def reextraire_arrete(
    projet_id: UUID,
    arrete_id: UUID,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> ArreteOut:
    """Relance OCR + extraction sur le PDF déjà lié à l'arrêté."""
    try:
        arrete = crud.lire_arrete(
            arrete_id,
            projet_id=projet_id,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    if not arrete.document_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucun PDF lié à cet arrêté",
        )

    try:
        pdf_bytes, _ctype, _name = get_document_content(arrete.document_id)
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture PDF impossible : {exc}",
        ) from exc

    try:
        return arrete_service.extraire_et_persister(
            pdf_bytes=pdf_bytes,
            arrete_id=arrete_id,
            projet_id=projet_id,
            document_id=arrete.document_id,
            type_form=arrete.type,
            reference_form=arrete.reference,
            date_notification_form=arrete.date_notification,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Re-extraction arrêté %s KO", arrete_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Extraction impossible : {exc}",
        ) from exc
