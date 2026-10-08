"""Routes baseline budget : consultation + figeage du budget initial."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from api.db.utilisateur import connect_utilisateur

router = APIRouter()


class BaselinePayload(BaseModel):
    libelle: str = Field(default="Budget initial", max_length=200)
    commentaire: str | None = None
    mode: Literal["completer", "ecraser"] = "completer"


@router.get("/projets/{projet_id}/budget/baseline")
def get_baseline_route(projet_id: UUID) -> dict[str, Any] | None:
    """Dernière baseline figée (None si jamais figée)."""
    try:
        with connect_utilisateur(row_factory=dict_row) as conn:
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
    """Fige montant_initial / annee_initiale dans occurrence_finance.

    mode='completer' : ne touche que les occurrences pas encore figées.
    mode='ecraser' : re-fige TOUT (validation d'un avenant).
    """
    try:
        with connect_utilisateur(row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bancarisation.occurrence_finance
                        (occurrence_id, montant_initial, annee_initiale)
                    SELECT o.id, f.montant_ht, o.annee
                    FROM bancarisation.occurrence o
                    LEFT JOIN bancarisation.occurrence_finance f ON f.occurrence_id = o.id
                    WHERE o.projet_id = %s
                      AND o.statut <> 'supprime'
                      AND (
                        %s = 'ecraser'
                        OR f.occurrence_id IS NULL
                        OR f.montant_initial IS NULL
                      )
                    ON CONFLICT (occurrence_id) DO UPDATE
                      SET montant_initial = excluded.montant_initial,
                          annee_initiale = excluded.annee_initiale
                    """,
                    (str(projet_id), payload.mode),
                )
                nb_figees = cur.rowcount

                cur.execute(
                    """
                    INSERT INTO bancarisation.budget_baseline
                        (projet_id, libelle, commentaire, mode, nb_occurrences, total_ht)
                    SELECT %s, %s, %s, %s,
                           count(*),
                           coalesce(sum(f.montant_ht), 0)
                    FROM bancarisation.occurrence o
                    LEFT JOIN bancarisation.occurrence_finance f ON f.occurrence_id = o.id
                    WHERE o.projet_id = %s
                      AND o.statut <> 'supprime'
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
