"""Routes baseline budget : consultation + figeage du budget initial."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from api.db.env import get_database_url

router = APIRouter()


class BaselinePayload(BaseModel):
    libelle: str = Field(default="Budget initial", max_length=200)
    commentaire: str | None = None
    mode: Literal["completer", "ecraser"] = "completer"


@router.get("/projets/{projet_id}/budget/baseline")
def get_baseline_route(projet_id: UUID) -> dict[str, Any] | None:
    """Dernière baseline figée (None si jamais figée)."""
    try:
        with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id::text,
                        projet_id::text,
                        figee_le::text,
                        libelle,
                        commentaire,
                        mode,
                        nb_occurrences,
                        total_ht::float8 AS total_ht
                    FROM bancarisation.budget_baseline
                    WHERE projet_id = %s
                    ORDER BY figee_le DESC
                    LIMIT 1
                    """,
                    (str(projet_id),),
                )
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture baseline impossible : {exc}",
        ) from exc


@router.post(
    "/projets/{projet_id}/budget/baseline",
    status_code=status.HTTP_201_CREATED,
)
def figer_baseline_route(projet_id: UUID, payload: BaselinePayload) -> dict[str, Any]:
    """Fige montant_initial / annee_initiale sur les occurrences du projet.

    mode='completer' : ne touche que les occurrences pas encore figées.
    mode='ecraser' : re-fige TOUT (validation d'un avenant).
    """
    filtre_completer = "AND montant_initial IS NULL" if payload.mode == "completer" else ""
    try:
        with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE bancarisation.occurrence
                    SET montant_initial = montant_ht,
                        annee_initiale  = annee
                    WHERE projet_id = %s
                      AND statut <> 'supprime'
                      {filtre_completer}
                    """,
                    (str(projet_id),),
                )
                nb_figees = cur.rowcount

                cur.execute(
                    """
                    INSERT INTO bancarisation.budget_baseline
                        (projet_id, libelle, commentaire, mode, nb_occurrences, total_ht)
                    SELECT %s, %s, %s, %s,
                           count(*),
                           coalesce(sum(montant_ht), 0)
                    FROM bancarisation.occurrence
                    WHERE projet_id = %s
                      AND statut <> 'supprime'
                    RETURNING
                        id::text,
                        projet_id::text,
                        figee_le::text,
                        libelle,
                        commentaire,
                        mode,
                        nb_occurrences,
                        total_ht::float8 AS total_ht
                    """,
                    (
                        str(projet_id),
                        payload.libelle,
                        payload.commentaire,
                        payload.mode,
                        str(projet_id),
                    ),
                )
                row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Figeage baseline impossible : {exc}",
        ) from exc

    result = dict(row) if row else {}
    result["nb_figees"] = nb_figees
    return result
