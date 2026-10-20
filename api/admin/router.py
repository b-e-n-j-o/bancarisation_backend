from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from api.admin import service
from api.db.utilisateur import connect_utilisateur_dict

router = APIRouter()
log = logging.getLogger(__name__)


def _echec(exc: Exception, message: str) -> HTTPException:
    log.exception("%s", message)
    detail = str(exc).strip() or message
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail)


class OrganisationCreate(BaseModel):
    nom: str = Field(min_length=1, max_length=255)
    type: str = "entite"
    nature: str = "bureau_etudes"
    parent_id: UUID | None = None
    manager_email: str


class AccesInvite(BaseModel):
    projet_id: UUID
    niveau: str = Field(pattern="^(lecteur|contributeur|gestionnaire)$")
    voir_finances: bool = False
    qualite: str | None = None


class InvitationBody(BaseModel):
    email: str
    type: str = "bureau"
    role: str | None = "membre"
    portee: str = "entite"
    projets: list[AccesInvite] = []


class RoleMembreBody(BaseModel):
    role: str


def _est_admin_plateforme() -> bool:
    with connect_utilisateur_dict() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT admin_plateforme
                FROM bancarisation.profils
                WHERE utilisateur_id = auth.uid()
                """
            )
            row = cur.fetchone()
    return bool(row and row.get("admin_plateforme"))


@router.get("/admin/organisations")
def lister_organisations() -> list[dict[str, Any]]:
    try:
        return service.lister_organisations()
    except HTTPException:
        raise
    except Exception as exc:
        raise _echec(exc, "Lecture des organisations impossible") from exc


@router.post("/admin/organisations", status_code=status.HTTP_201_CREATED)
def creer_organisation(body: OrganisationCreate) -> dict[str, Any]:
    try:
        return service.creer_organisation(
            body.nom,
            body.type,
            body.parent_id,
            str(body.manager_email),
            body.nature,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _echec(exc, "Création impossible") from exc


@router.get("/organisations/{organisation_id}/membres")
def lister_membres(organisation_id: UUID) -> list[dict[str, Any]]:
    return service.lister_membres(organisation_id)


@router.get("/organisations/{organisation_id}/invitations")
def lister_invitations(organisation_id: UUID) -> list[dict[str, Any]]:
    return service.lister_invitations(organisation_id)


@router.post("/organisations/{organisation_id}/invitations", status_code=status.HTTP_201_CREATED)
def inviter(organisation_id: UUID, body: InvitationBody) -> dict[str, Any]:
    acteur = service._exiger_admin_org_ou_plateforme(organisation_id)
    role = body.role
    if body.type == "bureau":
        role = role or "membre"
        service._verifier_role_attribuable(organisation_id, role, _est_admin_plateforme())
    else:
        role = None
    try:
        return service.inviter(
            str(body.email),
            organisation_id,
            role,
            body.portee,
            acteur,
            type_personne=body.type,
            projets=[p.model_dump() for p in body.projets],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _echec(exc, "Invitation impossible") from exc


@router.patch("/organisations/{organisation_id}/membres/{utilisateur_id}")
def changer_role_membre(
    organisation_id: UUID,
    utilisateur_id: UUID,
    body: RoleMembreBody,
) -> dict[str, Any]:
    return service.changer_role_membre(organisation_id, utilisateur_id, body.role)


@router.post("/invitations/{invitation_id}/renvoyer")
def renvoyer(invitation_id: UUID) -> dict[str, Any]:
    return service.renvoyer(invitation_id)


@router.post("/invitations/{invitation_id}/revoquer")
def revoquer(invitation_id: UUID) -> dict[str, Any]:
    return service.revoquer(invitation_id)
