"""Routes API — appariement prescriptions ↔ échéances."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from api.ocr.match_prescriptions import crud, service
from api.ocr.match_prescriptions.schemas import (
    AppariementEtatOut,
    ApparierResultOut,
    LienBody,
)
from api.parc.deps import MembreContext, get_membre_context

logger = logging.getLogger("match_prescriptions.router")

router = APIRouter(prefix="/match-prescriptions", tags=["match-prescriptions"])


@router.get(
    "/projets/{projet_id}/arretes/{arrete_id}/etat",
    response_model=AppariementEtatOut,
)
def get_etat(
    projet_id: UUID,
    arrete_id: UUID,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> AppariementEtatOut:
    try:
        return crud.lire_etat(
            projet_id,
            arrete_id,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post(
    "/projets/{projet_id}/arretes/{arrete_id}/apparier",
    response_model=ApparierResultOut,
)
def post_apparier(
    projet_id: UUID,
    arrete_id: UUID,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> ApparierResultOut:
    """Déclenchement manuel : LLM → propositions mode='ia'."""
    try:
        return service.lancer_appariement(
            projet_id,
            arrete_id,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Appariement KO")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Appariement impossible : {exc}",
        ) from exc


@router.post(
    "/projets/{projet_id}/liens/valider",
    response_model=AppariementEtatOut,
)
def post_valider(
    projet_id: UUID,
    body: LienBody,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> AppariementEtatOut:
    try:
        return crud.action_valider_lien(
            projet_id,
            body.prescription_id,
            body.echeance_id,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post(
    "/projets/{projet_id}/liens/rejeter",
    response_model=AppariementEtatOut,
)
def post_rejeter(
    projet_id: UUID,
    body: LienBody,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> AppariementEtatOut:
    try:
        return crud.action_rejeter_proposition(
            projet_id,
            body.prescription_id,
            body.echeance_id,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post(
    "/projets/{projet_id}/liens/retirer",
    response_model=AppariementEtatOut,
)
def post_retirer(
    projet_id: UUID,
    body: LienBody,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> AppariementEtatOut:
    """Retire un lien déjà validé (mode user)."""
    try:
        return crud.action_retirer_validation(
            projet_id,
            body.prescription_id,
            body.echeance_id,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
