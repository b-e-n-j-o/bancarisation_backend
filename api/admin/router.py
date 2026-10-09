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


class InvitationBody(BaseModel):
    email: str
    role: str = "membre"
    portee: str = "entite"


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
    service._verifier_role_attribuable(organisation_id, body.role, _est_admin_plateforme())
    try:
        return service.inviter(
            str(body.email),
            organisation_id,
            body.role,
            body.portee,
            acteur,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invitation impossible : {exc}") from exc


@router.post("/invitations/{invitation_id}/renvoyer")
def renvoyer(invitation_id: UUID) -> dict[str, Any]:
    return service.renvoyer(invitation_id)


@router.post("/invitations/{invitation_id}/revoquer")
def revoquer(invitation_id: UUID) -> dict[str, Any]:
    return service.revoquer(invitation_id)
