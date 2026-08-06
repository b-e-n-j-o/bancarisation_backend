from datetime import date
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from .crud_projet import (
    CreateProjetPayload,
    ProjetCrudError,
    UpdateProjetPayload,
    creer_projet,
    lire_projet,
    lister_projets,
    mettre_a_jour_projet,
    supprimer_projet,
)


router = APIRouter()

TypeDispositif = Literal["obligation", "site_credits"]


class ProjetCreateRequest(BaseModel):
    nom: str = Field(min_length=1, max_length=255)
    reference_interne: Optional[str] = Field(default=None, max_length=255)
    commune: Optional[str] = Field(default=None, max_length=255)
    departement: Optional[str] = Field(default=None, min_length=2, max_length=3)
    date_decision: Optional[date] = None
    duree_annees: Optional[int] = Field(default=None, ge=1, le=99)
    type_procedure: Optional[str] = Field(default=None, max_length=255)
    type_dispositif: TypeDispositif = "obligation"

    @field_validator("nom")
    @classmethod
    def strip_nom(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Champ obligatoire vide.")
        return cleaned

    @field_validator("reference_interne", "commune", "type_procedure")
    @classmethod
    def strip_optional(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("departement")
    @classmethod
    def validate_departement(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if not cleaned:
            return None
        if len(cleaned) not in (2, 3):
            raise ValueError("Code département invalide.")
        return cleaned


class ProjetCreateResponse(BaseModel):
    id: UUID


class ProjetResponse(BaseModel):
    data: dict[str, Any]


class ProjetUpdateRequest(BaseModel):
    nom: Optional[str] = Field(default=None, min_length=1, max_length=255)
    reference_interne: Optional[str] = Field(default=None, max_length=255)
    commune: Optional[str] = Field(default=None, max_length=255)
    departement: Optional[str] = Field(default=None, min_length=2, max_length=3)
    date_decision: Optional[date] = None
    duree_annees: Optional[int] = Field(default=None, ge=1, le=99)
    type_procedure: Optional[str] = Field(default=None, max_length=255)
    type_dispositif: Optional[TypeDispositif] = None
    partager_budget_dreal: Optional[bool] = None

    @field_validator("nom", "reference_interne")
    @classmethod
    def strip_update_required_if_provided(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Champ vide non autorisé.")
        return cleaned

    @field_validator("departement")
    @classmethod
    def validate_departement(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if len(cleaned) not in (2, 3):
            raise ValueError("Code département invalide.")
        return cleaned


@router.post(
    "/projets",
    response_model=ProjetCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_projet_route(payload: ProjetCreateRequest) -> ProjetCreateResponse:
    try:
        project_id = creer_projet(
            CreateProjetPayload(
                nom=payload.nom,
                reference_interne=payload.reference_interne,
                commune=payload.commune,
                departement=payload.departement,
                date_decision=payload.date_decision,
                duree_annees=payload.duree_annees,
                type_procedure=payload.type_procedure,
                type_dispositif=payload.type_dispositif,
            )
        )
    except ProjetCrudError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return ProjetCreateResponse(id=project_id)


@router.get("/projets", response_model=list[dict[str, Any]])
def list_projets_route(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    try:
        return lister_projets(limit=limit, offset=offset)
    except ProjetCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/projets/{projet_id}", response_model=ProjetResponse)
def get_projet_route(projet_id: UUID) -> ProjetResponse:
    try:
        data = lire_projet(projet_id)
    except ProjetCrudError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProjetResponse(data=data)


@router.patch("/projets/{projet_id}", response_model=ProjetResponse)
def update_projet_route(projet_id: UUID, payload: ProjetUpdateRequest) -> ProjetResponse:
    try:
        data = mettre_a_jour_projet(
            projet_id,
            UpdateProjetPayload(
                nom=payload.nom,
                reference_interne=payload.reference_interne,
                commune=payload.commune,
                departement=payload.departement,
                date_decision=payload.date_decision,
                duree_annees=payload.duree_annees,
                type_procedure=payload.type_procedure,
                type_dispositif=payload.type_dispositif,
                partager_budget_dreal=payload.partager_budget_dreal,
            ),
        )
    except ProjetCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ProjetResponse(data=data)


@router.delete("/projets/{projet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_projet_route(projet_id: UUID) -> None:
    try:
        supprimer_projet(projet_id)
    except ProjetCrudError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
