"""Routes du canal de dialogue DREAL ↔ bureau d'études.

Emplacement : backend/api/dialogue/router.py

À monter dans l'app comme les autres modules :
    from api.dialogue.router import router as dialogue_router
    app.include_router(dialogue_router, prefix="/api")
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from .crud import (
    DialogueError,
    ajouter_message,
    changer_statut_demande,
    creer_demande,
    lire_demande,
    lister_activite,
    lister_demandes,
    lister_demandes_parc,
)

router = APIRouter()

Acteur = Literal["dreal", "be"]


class DemandeCreate(BaseModel):
    objet: str = Field(min_length=1, max_length=300)
    corps: str = Field(min_length=1)
    acteur: Acteur = "dreal"
    auteur_nom: str | None = None
    occurrence_id: UUID | None = None
    annee: int | None = None
    signal_code: str | None = None
    documents_ids: list[UUID] = Field(default_factory=list)


class MessageCreate(BaseModel):
    acteur: Acteur
    corps: str = Field(min_length=1)
    auteur_nom: str | None = None
    documents_ids: list[UUID] = Field(default_factory=list)


class StatutPayload(BaseModel):
    statut: Literal["ouverte", "repondue", "close"]


def _erreur(exc: DialogueError, code: int = status.HTTP_400_BAD_REQUEST):
    return HTTPException(status_code=code, detail=str(exc))


# ---------------------------------------------------------------------------
# Niveau projet
# ---------------------------------------------------------------------------

@router.get("/projets/{projet_id}/demandes")
def lister_demandes_route(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return lister_demandes(projet_id)
    except DialogueError as exc:
        raise _erreur(exc) from exc


@router.post("/projets/{projet_id}/demandes", status_code=status.HTTP_201_CREATED)
def creer_demande_route(projet_id: UUID, payload: DemandeCreate) -> dict[str, Any]:
    try:
        return creer_demande(
            projet_id,
            payload.objet,
            payload.corps,
            payload.acteur,
            auteur_nom=payload.auteur_nom,
            occurrence_id=payload.occurrence_id,
            annee=payload.annee,
            signal_code=payload.signal_code,
            documents_ids=[str(d) for d in payload.documents_ids],
        )
    except DialogueError as exc:
        raise _erreur(exc) from exc


@router.get("/projets/{projet_id}/activite")
def activite_route(
    projet_id: UUID,
    limite: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Fil conducteur : demandes, messages, bilans validés, baselines figées."""
    try:
        return lister_activite(projet_id, limite=limite)
    except DialogueError as exc:
        raise _erreur(exc) from exc


# ---------------------------------------------------------------------------
# Niveau demande
# ---------------------------------------------------------------------------

@router.get("/demandes/{demande_id}")
def lire_demande_route(demande_id: UUID) -> dict[str, Any]:
    try:
        return lire_demande(demande_id)
    except DialogueError as exc:
        raise _erreur(exc, status.HTTP_404_NOT_FOUND) from exc


@router.post("/demandes/{demande_id}/messages", status_code=status.HTTP_201_CREATED)
def ajouter_message_route(demande_id: UUID, payload: MessageCreate) -> dict[str, Any]:
    try:
        return ajouter_message(
            demande_id,
            payload.acteur,
            payload.corps,
            auteur_nom=payload.auteur_nom,
            documents_ids=[str(d) for d in payload.documents_ids],
        )
    except DialogueError as exc:
        raise _erreur(exc) from exc


@router.post("/demandes/{demande_id}/statut")
def changer_statut_route(demande_id: UUID, payload: StatutPayload) -> dict[str, Any]:
    try:
        return changer_statut_demande(demande_id, payload.statut)
    except DialogueError as exc:
        raise _erreur(exc) from exc


# ---------------------------------------------------------------------------
# Niveau parc (section suivi DREAL)
# ---------------------------------------------------------------------------

@router.get("/demandes")
def lister_demandes_parc_route(
    statut: str | None = Query(default=None),
    limite: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Toutes les demandes du parc, les plus anciennes sans réponse en tête."""
    try:
        return lister_demandes_parc(statut=statut, limite=limite)
    except DialogueError as exc:
        raise _erreur(exc) from exc
