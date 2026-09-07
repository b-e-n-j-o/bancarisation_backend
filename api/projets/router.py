from datetime import date
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from api.parc.deps import MembreContext, get_membre_context
from api.controle.crud import lister_arretes_projet
from api.controle.schemas import ArreteOut

from .crud_projet import (
    ORGANISATION_ID_V0,
    CreateProjetPayload,
    ProjetCrudError,
    UpdateProjetPayload,
    compter_catalogue,
    creer_projet,
    lire_organisation,
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


class SessionResponse(BaseModel):
    user_id: UUID | None = None
    role: str
    organisation_id: UUID | None = None
    organisation_nom: str | None = None


class CatalogueCountsResponse(BaseModel):
    utilisateur: int
    geomce: int


@router.get("/session", response_model=SessionResponse)
def session_route(
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> SessionResponse:
    """Identité courante (header X-User-Id) + organisation rattachée.

    Sans auth JWT : opérateur → son org ; contrôleur démo → org V0 si présente.
    """
    org_id = membre.organisation_id
    org_nom = membre.organisation_nom
    if org_id is None:
        try:
            fallback = lire_organisation(UUID(ORGANISATION_ID_V0))
        except ProjetCrudError:
            fallback = None
        if fallback:
            org_id = UUID(str(fallback["id"]))
            org_nom = str(fallback["nom"])
    elif not org_nom:
        try:
            org = lire_organisation(org_id)
        except ProjetCrudError:
            org = None
        if org:
            org_nom = str(org["nom"])

    return SessionResponse(
        user_id=membre.user_id,
        role=membre.role,
        organisation_id=org_id,
        organisation_nom=org_nom,
    )


@router.get("/projets/catalogue-counts", response_model=CatalogueCountsResponse)
def catalogue_counts_route() -> CatalogueCountsResponse:
    try:
        counts = compter_catalogue()
    except ProjetCrudError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return CatalogueCountsResponse(
        utilisateur=counts["utilisateur"],
        geomce=counts["geomce"],
    )


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


@router.get("/projets/{projet_id}/arretes", response_model=list[ArreteOut])
def get_arretes_projet(projet_id: UUID) -> list[ArreteOut]:
    """Obligations extraites des arrêtés — accessible au BE (pas de gate DREAL)."""
    try:
        return lister_arretes_projet(projet_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture des arrêtés impossible : {exc}",
        ) from exc


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
