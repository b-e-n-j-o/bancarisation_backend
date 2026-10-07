from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from api.db.supabase import get_supabase_admin
from api.db.utilisateur import connect_utilisateur_dict
from auth.errors import http_from_db

router = APIRouter(prefix="/admin")


class OrganisationCreate(BaseModel):
    nom: str = Field(min_length=1, max_length=255)
    type: str = "entite"
    nature: str = "bureau_etudes"
    parent_id: UUID | None = None


class InvitationBody(BaseModel):
    email: str
    organisation_id: UUID
    role: str = "membre"


def _exiger_admin_plateforme() -> UUID:
    with connect_utilisateur_dict() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT utilisateur_id, admin_plateforme
                FROM bancarisation.profils
                WHERE utilisateur_id = auth.uid()
                """
            )
            row = cur.fetchone()
    if not row or not row.get("admin_plateforme"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Réservé à l'administration plateforme.",
        )
    return UUID(str(row["utilisateur_id"]))


def _exiger_admin_org(organisation_id: UUID) -> None:
    with connect_utilisateur_dict() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT prive.role_org(%s) AS role",
                (str(organisation_id),),
            )
            row = cur.fetchone()
    if not row or row.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Réservé à l'admin de l'organisation.",
        )


@router.post("/organisations", status_code=status.HTTP_201_CREATED)
def creer_organisation(body: OrganisationCreate) -> dict[str, Any]:
    acteur = _exiger_admin_plateforme()
    client = get_supabase_admin()
    payload: dict[str, Any] = {
        "nom": body.nom.strip(),
        "type": body.type,
        "nature": body.nature,
        "statut": "active",
    }
    if body.parent_id is not None:
        payload["parent_id"] = str(body.parent_id)
    try:
        resp = client.schema("bancarisation").table("organisations").insert(payload).execute()
    except Exception as exc:
        raise http_from_db(exc) from exc
    data = (resp.data or [None])[0]
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Création d'organisation échouée.")
    with connect_utilisateur_dict() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bancarisation.journal_audit
                    (acteur_id, organisation_id, action, details)
                VALUES (%s, %s, 'admin.organisation.creer', jsonb_build_object('nom', %s))
                """,
                (str(acteur), str(data.get("id")), body.nom),
            )
        conn.commit()
    return data


@router.post("/invitations", status_code=status.HTTP_201_CREATED)
def inviter(body: InvitationBody) -> dict[str, Any]:
    _exiger_admin_org(body.organisation_id)
    client = get_supabase_admin()
    try:
        res = client.auth.admin.invite_user_by_email(body.email)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invitation impossible : {exc}",
        ) from exc
    user = getattr(res, "user", None) or (res if isinstance(res, dict) else {})
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    if uid:
        try:
            client.schema("bancarisation").table("membre_organisation").insert(
                {
                    "utilisateur_id": str(uid),
                    "organisation_id": str(body.organisation_id),
                    "role": body.role if body.role in ("admin", "membre") else "membre",
                    "statut": "invite",
                    "portee": "entite",
                }
            ).execute()
        except Exception:
            pass
    return {"ok": True, "email": body.email}
