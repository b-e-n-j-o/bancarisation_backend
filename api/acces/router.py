from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, status
from psycopg import Error as PsycopgError
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from api.db.env import get_database_url
from api.db.utilisateur import connect_utilisateur_dict
from auth.errors import http_from_db

router = APIRouter()

QUALITES = {
    "operateur",
    "maitre_ouvrage",
    "bureau_etudes",
    "controle",
    "ancien_proprietaire",
    "prestataire",
}


class ProjetAccesCreate(BaseModel):
    utilisateur_id: UUID | None = None
    organisation_id: UUID | None = None
    qualite: str | None = None
    niveau: str = Field(pattern="^(lecteur|contributeur|gestionnaire)$")
    voir_finances: bool = False


class ProjetAccesMaj(BaseModel):
    niveau: str = Field(pattern="^(lecteur|contributeur|gestionnaire)$")
    voir_finances: bool = False
    qualite: str | None = None


def _fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        with connect_utilisateur_dict() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
    except PsycopgError as exc:
        raise http_from_db(exc) from exc


def _completer_identites(rows: list[dict[str, Any]]) -> None:
    """E-mail et nom d'organisation : auth.users n'est pas lisible sous la RLS utilisateur."""
    uids = [str(r["utilisateur_id"]) for r in rows if r.get("utilisateur_id")]
    oids = [str(r["organisation_id"]) for r in rows if r.get("organisation_id")]
    if not uids and not oids:
        return
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            emails: dict[str, str] = {}
            noms: dict[str, str] = {}
            if uids:
                cur.execute(
                    "SELECT id::text AS id, email FROM auth.users WHERE id = ANY(%s::uuid[])",
                    (uids,),
                )
                emails = {r["id"]: r["email"] for r in cur.fetchall()}
            if oids:
                cur.execute(
                    "SELECT id::text AS id, nom FROM bancarisation.organisations WHERE id = ANY(%s::uuid[])",
                    (oids,),
                )
                noms = {r["id"]: r["nom"] for r in cur.fetchall()}
    for row in rows:
        uid = str(row["utilisateur_id"]) if row.get("utilisateur_id") else None
        oid = str(row["organisation_id"]) if row.get("organisation_id") else None
        if uid:
            row["email"] = emails.get(uid)
        if oid:
            row["organisation_nom"] = noms.get(oid)


def _qualite_connue(qualite: str | None) -> None:
    if qualite is not None and qualite not in QUALITES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Qualité inconnue.")


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
    _completer_identites(rows)
    return rows


@router.post("/projets/{projet_id}/acces", status_code=status.HTTP_201_CREATED)
def creer_acces(projet_id: UUID, body: ProjetAccesCreate) -> dict[str, Any]:
    if (body.utilisateur_id is None) == (body.organisation_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indiquer soit utilisateur_id, soit organisation_id.",
        )
    _qualite_connue(body.qualite)
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
    except PsycopgError as exc:
        if getattr(exc, "sqlstate", None) == "23505":
            raise HTTPException(status.HTTP_409_CONFLICT, "Cet accès existe déjà.") from exc
        raise http_from_db(exc) from exc
    if not row:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Droits insuffisants.")
    return row


@router.patch("/projets/{projet_id}/acces/{acces_id}")
def modifier_acces(projet_id: UUID, acces_id: UUID, body: ProjetAccesMaj) -> dict[str, Any]:
    _qualite_connue(body.qualite)
    try:
        with connect_utilisateur_dict() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bancarisation.projet_acces
                    SET niveau = %s, voir_finances = %s, qualite = %s
                    WHERE id = %s AND projet_id = %s
                    RETURNING *
                    """,
                    (
                        body.niveau,
                        body.voir_finances,
                        body.qualite,
                        str(acces_id),
                        str(projet_id),
                    ),
                )
                row = cur.fetchone()
    except PsycopgError as exc:
        raise http_from_db(exc) from exc
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Partage introuvable.")
    return row


@router.delete(
    "/projets/{projet_id}/acces/{acces_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
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
    except PsycopgError as exc:
        raise http_from_db(exc) from exc
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Partage introuvable.")
    return None
