from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from psycopg import Error as PsycopgError
from api.db.utilisateur import connect_utilisateur_dict
from auth.errors import http_from_db

router = APIRouter()


class ProjetAccesCreate(BaseModel):
    utilisateur_id: UUID | None = None
    organisation_id: UUID | None = None
    qualite: str | None = None
    niveau: str = Field(pattern="^(lecteur|contributeur|gestionnaire)$")
    voir_finances: bool = False


def _fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        with connect_utilisateur_dict() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
    except PsycopgError as exc:
        raise http_from_db(exc) from exc


@router.get("/projets/{projet_id}/acces")
def lister_acces(projet_id: UUID) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        SELECT id, projet_id, utilisateur_id, organisation_id, qualite, niveau,
               voir_finances, expire_le, accorde_par
        FROM bancarisation.projet_acces
        WHERE projet_id = %s
        ORDER BY id DESC
        """,
        (str(projet_id),),
    )
    return rows


@router.post("/projets/{projet_id}/acces", status_code=status.HTTP_201_CREATED)
def creer_acces(projet_id: UUID, body: ProjetAccesCreate) -> dict[str, Any]:
    if (body.utilisateur_id is None) == (body.organisation_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indiquer soit utilisateur_id, soit organisation_id.",
        )
    try:
        with connect_utilisateur_dict() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bancarisation.projet_acces
                        (projet_id, utilisateur_id, organisation_id, qualite, niveau,
                         voir_finances, accorde_par)
                    VALUES (%s, %s, %s, %s, %s, %s, auth.uid())
                    RETURNING *
                    """,
                    (
                        str(projet_id),
                        str(body.utilisateur_id) if body.utilisateur_id else None,
                        str(body.organisation_id) if body.organisation_id else None,
                        body.qualite,
                        body.niveau,
                        body.voir_finances,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
    except PsycopgError as exc:
        raise http_from_db(exc) from exc
    if not row:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Droits insuffisants.")
    return row


@router.delete("/projets/{projet_id}/acces/{acces_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_acces(projet_id: UUID, acces_id: UUID) -> None:
    try:
        with connect_utilisateur_dict() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM bancarisation.projet_acces
                    WHERE id = %s AND projet_id = %s
                    RETURNING id
                    """,
                    (str(acces_id), str(projet_id)),
                )
                row = cur.fetchone()
            conn.commit()
    except PsycopgError as exc:
        raise http_from_db(exc) from exc
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Partage introuvable.")
