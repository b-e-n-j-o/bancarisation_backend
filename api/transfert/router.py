from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel
from psycopg import Error as PsycopgError
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from api.db.utilisateur import connect_utilisateur_dict
from api.export.service import executer_export
from auth.deps import get_claims
from auth.errors import http_from_db

router = APIRouter()


class DemandeTransfertBody(BaseModel):
    org_cible: UUID
    garder_lecture_source: bool = True
    finances_source: bool = False
    commentaire: str | None = None


class RefusTransfertBody(BaseModel):
    motif: str | None = None


@router.post("/projets/{projet_id}/transferts", status_code=status.HTTP_201_CREATED)
def demander(projet_id: UUID, body: DemandeTransfertBody, taches: BackgroundTasks) -> dict[str, str | None]:
    claims = get_claims()
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
    export_id = _lancer_archive(projet_id, claims, taches)
    if export_id:
        try:
            with connect_utilisateur_dict() as conn:
                conn.execute(
                    "select bancarisation.rattacher_export_transfert(%s, %s)",
                    (str(row["id"]), export_id),
                )
        except PsycopgError:
            export_id = None
    return {"id": str(row["id"]), "export_id": export_id}


@router.get("/projets/{projet_id}/transferts")
def lister(projet_id: UUID) -> dict[str, list]:
    with connect_utilisateur_dict() as conn:
        rows = conn.execute(
            """
            select id::text, statut, export_id::text, projet_nom, demande_le
              from bancarisation.transfert_projet
             where projet_id = %s
             order by demande_le desc
            """,
            (str(projet_id),),
        ).fetchall()
    return {"transferts": [dict(r) for r in rows]}


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


def _lancer_archive(projet_id: UUID, claims: dict, taches: BackgroundTasks) -> str | None:
    """Export complet au nom du demandeur, tant que son organisation est propriétaire."""
    user_id = str(claims["sub"])
    try:
        with connect_utilisateur_dict() as conn:
            projet = conn.execute(
                "select organisation_id::text from bancarisation.projets where id = %s",
                (str(projet_id),),
            ).fetchone()
            if not projet:
                return None
            cree = conn.execute(
                """
                insert into bancarisation.export_projet
                  (projet_id, organisation_id, demande_par, options)
                values (%s, %s, %s, %s)
                returning id::text
                """,
                (
                    str(projet_id),
                    projet["organisation_id"],
                    user_id,
                    Jsonb({
                        "inclure_budget": True,
                        "inclure_documents": True,
                        "annee_min": None,
                        "annee_max": None,
                        "inclure_supprimees": False,
                        "conservation_jours": 30,
                    }),
                ),
            ).fetchone()
    except UniqueViolation:
        return None
    except PsycopgError:
        return None
    if not cree:
        return None
    taches.add_task(executer_export, cree["id"], user_id, claims.get("email"))
    return str(cree["id"])
