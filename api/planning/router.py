from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from .planning import (
    CreateActionPayload,
    CreatePrestaPayload,
    CreateUnitePayload,
    PlanningCrudError,
    UpdateActionPayload,
    UpdatePrestaPayload,
    UpdateUnitePayload,
    creer_action,
    creer_prestataire,
    creer_unite,
    lire_action,
    lister_actions,
    lister_prestataires,
    lister_unites,
    mettre_a_jour_action,
    mettre_a_jour_prestataire,
    mettre_a_jour_unite,
    supprimer_action,
    supprimer_unite,
)

router = APIRouter()


class ActionCreateRequest(BaseModel):
    projet_id: UUID
    annee: int = Field(ge=2000, le=2100)
    categorie: str
    libelle_prestation: str = Field(min_length=1, max_length=500)
    statut: str = "projete"
    thema_code: Optional[str] = None
    cout_ht_prevu: Optional[float] = None
    prestataire_id: Optional[UUID] = None
    unit_id: Optional[UUID] = None
    note: Optional[str] = None


class ActionUpdateRequest(BaseModel):
    annee: Optional[int] = Field(default=None, ge=2000, le=2100)
    categorie: Optional[str] = None
    libelle_prestation: Optional[str] = Field(default=None, min_length=1, max_length=500)
    statut: Optional[str] = None
    thema_code: Optional[str] = None
    cout_ht_prevu: Optional[float] = None
    prestataire_id: Optional[UUID] = None
    unit_id: Optional[UUID] = None
    note: Optional[str] = None


class IdResponse(BaseModel):
    id: UUID


class UniteCreateRequest(BaseModel):
    projet_id: UUID
    code: str = Field(min_length=1, max_length=30)
    type_milieu: str
    libelle: Optional[str] = None
    description: Optional[str] = None


class UniteUpdateRequest(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=30)
    type_milieu: Optional[str] = None
    libelle: Optional[str] = None
    description: Optional[str] = None


class PrestataireCreateRequest(BaseModel):
    nom: str = Field(min_length=1, max_length=255)
    role_defaut: str = "sous_traitant"
    siret: Optional[str] = None
    contact_nom: Optional[str] = None
    contact_email: Optional[str] = None


class PrestataireUpdateRequest(BaseModel):
    nom: Optional[str] = None
    role_defaut: Optional[str] = None
    siret: Optional[str] = None
    contact_nom: Optional[str] = None
    contact_email: Optional[str] = None
    actif: Optional[bool] = None


@router.get("/planning/actions", response_model=list[dict[str, Any]])
def list_actions_route(projet_id: UUID = Query(...)) -> list[dict[str, Any]]:
    try:
        return lister_actions(projet_id)
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/planning/actions/{action_id}", response_model=dict[str, Any])
def get_action_route(action_id: UUID) -> dict[str, Any]:
    try:
        return lire_action(action_id)
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/planning/actions", response_model=IdResponse, status_code=status.HTTP_201_CREATED)
def create_action_route(payload: ActionCreateRequest) -> IdResponse:
    try:
        action_id = creer_action(
            CreateActionPayload(
                projet_id=payload.projet_id,
                annee=payload.annee,
                categorie=payload.categorie,
                libelle_prestation=payload.libelle_prestation,
                statut=payload.statut,
                thema_code=payload.thema_code,
                cout_ht_prevu=payload.cout_ht_prevu,
                prestataire_id=payload.prestataire_id,
                unit_id=payload.unit_id,
                note=payload.note,
            )
        )
        return IdResponse(id=action_id)
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/planning/actions/{action_id}", response_model=dict[str, Any])
def update_action_route(action_id: UUID, payload: ActionUpdateRequest) -> dict[str, Any]:
    try:
        return mettre_a_jour_action(
            action_id,
            UpdateActionPayload(
                annee=payload.annee,
                categorie=payload.categorie,
                libelle_prestation=payload.libelle_prestation,
                statut=payload.statut,
                thema_code=payload.thema_code,
                cout_ht_prevu=payload.cout_ht_prevu,
                prestataire_id=payload.prestataire_id,
                unit_id=payload.unit_id,
                note=payload.note,
            ),
        )
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/planning/actions/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_action_route(action_id: UUID) -> None:
    try:
        supprimer_action(action_id)
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/planning/unites", response_model=list[dict[str, Any]])
def list_unites_route(projet_id: UUID = Query(...)) -> list[dict[str, Any]]:
    try:
        return lister_unites(projet_id)
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/planning/unites", response_model=IdResponse, status_code=status.HTTP_201_CREATED)
def create_unite_route(payload: UniteCreateRequest) -> IdResponse:
    try:
        unit_id = creer_unite(
            CreateUnitePayload(
                projet_id=payload.projet_id,
                code=payload.code,
                type_milieu=payload.type_milieu,
                libelle=payload.libelle,
                description=payload.description,
            )
        )
        return IdResponse(id=unit_id)
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/planning/unites/{unite_id}", response_model=dict[str, Any])
def update_unite_route(unite_id: UUID, payload: UniteUpdateRequest) -> dict[str, Any]:
    try:
        return mettre_a_jour_unite(
            unite_id,
            UpdateUnitePayload(
                code=payload.code,
                type_milieu=payload.type_milieu,
                libelle=payload.libelle,
                description=payload.description,
            ),
        )
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/planning/unites/{unite_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_unite_route(unite_id: UUID) -> None:
    try:
        supprimer_unite(unite_id)
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/planning/prestataires", response_model=list[dict[str, Any]])
def list_prestataires_route() -> list[dict[str, Any]]:
    try:
        return lister_prestataires()
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/planning/prestataires", response_model=IdResponse, status_code=status.HTTP_201_CREATED)
def create_prestataire_route(payload: PrestataireCreateRequest) -> IdResponse:
    try:
        presta_id = creer_prestataire(
            CreatePrestaPayload(
                nom=payload.nom,
                role_defaut=payload.role_defaut,
                siret=payload.siret,
                contact_nom=payload.contact_nom,
                contact_email=payload.contact_email,
            )
        )
        return IdResponse(id=presta_id)
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/planning/prestataires/{presta_id}", response_model=dict[str, Any])
def update_prestataire_route(presta_id: UUID, payload: PrestataireUpdateRequest) -> dict[str, Any]:
    try:
        return mettre_a_jour_prestataire(
            presta_id,
            UpdatePrestaPayload(
                nom=payload.nom,
                role_defaut=payload.role_defaut,
                siret=payload.siret,
                contact_nom=payload.contact_nom,
                contact_email=payload.contact_email,
                actif=payload.actif,
            ),
        )
    except PlanningCrudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
