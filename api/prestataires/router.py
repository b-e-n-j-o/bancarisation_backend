"""Référentiel prestataires + rattachement par projet.

Connexion Postgres directe (psycopg), comme budget/baseline.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel

from . import service

router = APIRouter()


class PrestataireCreateAttach(BaseModel):
    """Attach existant OU création + attach."""

    prestataire_id: UUID | None = None
    nom: str | None = None
    siret: str | None = None
    adresse: str | None = None
    code_postal: str | None = None
    commune: str | None = None
    telephone: str | None = None
    email: str | None = None
    interlocuteur: str | None = None
    role: str | None = None


class PrestatairePatch(BaseModel):
    nom: str | None = None
    siret: str | None = None
    forme_juridique: str | None = None
    adresse: str | None = None
    code_postal: str | None = None
    commune: str | None = None
    departement: str | None = None
    email: str | None = None
    telephone: str | None = None
    interlocuteur: str | None = None
    notes: str | None = None
    actif: bool | None = None


@router.get("/prestataires")
def list_prestataires_route(
    q: str | None = Query(default=None, description="Recherche partielle nom / SIRET"),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict[str, Any]]:
    """Typeahead référentiel global (ajout depuis l’onglet Prestataires)."""
    try:
        return service.lister_global(q=q, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture prestataires impossible : {exc}",
        ) from exc


@router.get("/prestataires/{prestataire_id}")
def get_prestataire_route(prestataire_id: UUID) -> dict[str, Any]:
    try:
        row = service.get_prestataire(prestataire_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture prestataire impossible : {exc}",
        ) from exc
    if not row:
        raise HTTPException(status_code=404, detail="Prestataire introuvable.")
    return row


@router.patch("/prestataires/{prestataire_id}")
def patch_prestataire_route(
    prestataire_id: UUID,
    payload: PrestatairePatch,
) -> dict[str, Any]:
    champs = payload.model_dump(exclude_unset=True)
    if not champs:
        raise HTTPException(status_code=400, detail="Aucun champ à modifier.")
    try:
        row = service.patch_prestataire(prestataire_id, champs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Mise à jour prestataire impossible : {exc}",
        ) from exc
    if not row:
        raise HTTPException(status_code=404, detail="Prestataire introuvable.")
    return row


@router.get("/projets/{projet_id}/prestataires")
def list_projet_prestataires_route(
    projet_id: UUID,
    q: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """Liste projet (vue enrichie) — cards + typeahead budget."""
    try:
        return service.lister_projet(projet_id, q=q)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture prestataires projet impossible : {exc}",
        ) from exc


@router.post(
    "/projets/{projet_id}/prestataires",
    status_code=status.HTTP_201_CREATED,
)
def attach_projet_prestataire_route(
    projet_id: UUID,
    payload: PrestataireCreateAttach,
) -> dict[str, Any]:
    try:
        return service.rattacher(
            projet_id,
            prestataire_id=payload.prestataire_id,
            nom=payload.nom,
            siret=payload.siret,
            adresse=payload.adresse,
            code_postal=payload.code_postal,
            commune=payload.commune,
            telephone=payload.telephone,
            email=payload.email,
            interlocuteur=payload.interlocuteur,
            role=payload.role,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rattachement impossible : {exc}",
        ) from exc


@router.delete(
    "/projets/{projet_id}/prestataires/{prestataire_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def detach_projet_prestataire_route(
    projet_id: UUID,
    prestataire_id: UUID,
) -> Response:
    try:
        service.detacher(projet_id, prestataire_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retrait impossible : {exc}",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)