from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.db.utilisateur import connect_utilisateur_dict
from auth.errors import http_from_db
from psycopg import Error as PsycopgError

router = APIRouter()


class DemandeTransfertBody(BaseModel):
    org_cible: UUID
    garder_lecture_source: bool = True
    finances_source: bool = False
    commentaire: str | None = None


class RefusTransfertBody(BaseModel):
    motif: str | None = None


@router.post("/projets/{projet_id}/transferts", status_code=status.HTTP_201_CREATED)
def demander(projet_id: UUID, body: DemandeTransfertBody) -> dict[str, str]:
    try:
        with connect_utilisateur_dict() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT bancarisation.demander_transfert(%s, %s, %s, %s, %s) AS id
                    """,
                    (
                        str(projet_id),
                        str(body.org_cible),
                        body.garder_lecture_source,
                        body.finances_source,
                        body.commentaire,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
    except PsycopgError as exc:
        raise http_from_db(exc) from exc
    if not row:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Droits insuffisants.")
    return {"id": str(row["id"])}


@router.post("/transferts/{transfert_id}/accepter")
def accepter(transfert_id: UUID) -> dict[str, bool]:
    try:
        with connect_utilisateur_dict() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT bancarisation.accepter_transfert(%s)",
                    (str(transfert_id),),
                )
            conn.commit()
    except PsycopgError as exc:
        raise http_from_db(exc) from exc
    return {"ok": True}


@router.post("/transferts/{transfert_id}/refuser")
def refuser(transfert_id: UUID, body: RefusTransfertBody) -> dict[str, bool]:
    try:
        with connect_utilisateur_dict() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT bancarisation.refuser_transfert(%s, %s)",
                    (str(transfert_id), body.motif),
                )
            conn.commit()
    except PsycopgError as exc:
        raise http_from_db(exc) from exc
    return {"ok": True}


@router.post("/transferts/{transfert_id}/annuler")
def annuler(transfert_id: UUID) -> dict[str, bool]:
    try:
        with connect_utilisateur_dict() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT bancarisation.annuler_transfert(%s)",
                    (str(transfert_id),),
                )
            conn.commit()
    except PsycopgError as exc:
        raise http_from_db(exc) from exc
    return {"ok": True}
