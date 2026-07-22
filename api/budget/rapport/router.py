"""Routes bilan financier annuel (aperçu, génération, lecture, validation, PDF)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from .archiver import archiver_pdf_bilan
from .bilan import (
    BilanError,
    construire_bilan,
    generer_bilan,
    lire_bilan,
    lister_bilans,
    supprimer_bilan,
    valider_bilan,
)
from .pdf import nom_fichier, rendre_pdf

router = APIRouter()


class ValidationPayload(BaseModel):
    force: bool = False
    valide_par: str | None = Field(default=None, max_length=200)


@router.get("/projets/{projet_id}/bilans")
def lister_bilans_route(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return lister_bilans(projet_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture bilans impossible : {exc}",
        ) from exc


@router.get("/projets/{projet_id}/bilans/apercu")
def apercu_bilan_route(
    projet_id: UUID,
    annee: int = Query(..., ge=1900, le=2200),
) -> dict[str, Any]:
    """Construction à la volée SANS archivage (relecture avant génération)."""
    try:
        return construire_bilan(projet_id, annee)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Aperçu bilan impossible : {exc}",
        ) from exc


@router.get("/projets/{projet_id}/bilans/apercu.pdf")
def apercu_pdf_route(
    projet_id: UUID,
    annee: int = Query(..., ge=1900, le=2200),
) -> Response:
    """Aperçu PDF sans archivage — non déposé dans le bucket."""
    try:
        snapshot = construire_bilan(projet_id, annee)
        bilan = {"donnees": snapshot, "version": 0, "statut": "valide"}
        return Response(
            content=rendre_pdf(bilan),
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="bilan-apercu.pdf"'},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Aperçu PDF impossible : {exc}",
        ) from exc


@router.post(
    "/projets/{projet_id}/bilans",
    status_code=status.HTTP_201_CREATED,
)
def generer_bilan_route(
    projet_id: UUID,
    annee: int = Query(..., ge=1900, le=2200),
    genere_par: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    try:
        return generer_bilan(projet_id, annee, genere_par=genere_par)
    except BilanError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Génération bilan impossible : {exc}",
        ) from exc


@router.get("/bilans/{rapport_id}")
def lire_bilan_route(rapport_id: UUID) -> dict[str, Any]:
    try:
        return lire_bilan(rapport_id)
    except BilanError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture bilan impossible : {exc}",
        ) from exc


@router.get("/bilans/{rapport_id}/pdf")
def bilan_pdf_route(rapport_id: UUID) -> Response:
    """PDF d'un bilan archivé : rendu du snapshot, jamais un recalcul."""
    try:
        bilan = lire_bilan(rapport_id)
    except BilanError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    try:
        return Response(
            content=rendre_pdf(bilan),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{nom_fichier(bilan)}"'},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rendu PDF impossible : {exc}",
        ) from exc


@router.post("/bilans/{rapport_id}/pdf/archiver")
def archiver_pdf_route(
    rapport_id: UUID,
    remplacer: bool = Query(default=True),
) -> dict[str, Any]:
    """Dépose le PDF dans le bucket (…/bilans/) + table documents, lie document_id."""
    try:
        return archiver_pdf_bilan(rapport_id, remplacer=remplacer)
    except BilanError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Archivage PDF impossible : {exc}",
        ) from exc


@router.post("/bilans/{rapport_id}/valider")
def valider_bilan_route(rapport_id: UUID, payload: ValidationPayload) -> dict[str, Any]:
    try:
        return valider_bilan(
            rapport_id,
            valide_par=payload.valide_par,
            force=payload.force,
        )
    except BilanError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Validation bilan impossible : {exc}",
        ) from exc


@router.delete("/bilans/{rapport_id}", status_code=status.HTTP_200_OK)
def supprimer_bilan_route(
    rapport_id: UUID,
    acteur: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    """Supprime un bilan généré et journalise l'action."""
    try:
        return supprimer_bilan(rapport_id, acteur=acteur)
    except BilanError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Suppression bilan impossible : {exc}",
        ) from exc
