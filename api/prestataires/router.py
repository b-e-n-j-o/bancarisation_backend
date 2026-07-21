"""Référentiel prestataires bancarisation — liste + recherche textuelle.

Connexion Postgres directe (psycopg), comme budget/baseline :
en local, passer par le tunnel SSH vers le VPS (DATABASE_URL → 127.0.0.1:5432).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, status
from psycopg.rows import dict_row

from api.db.env import get_database_url

router = APIRouter()


@router.get("/prestataires")
def list_prestataires_route(
    q: str | None = Query(default=None, description="Recherche partielle sur le nom"),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict[str, Any]]:
    """Recherche typeahead : filtre ILIKE, limité (jamais toute la table)."""
    terme = (q or "").strip()
    try:
        with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                if terme:
                    cur.execute(
                        """
                        SELECT id::text, nom
                        FROM bancarisation.prestataires
                        WHERE nom ILIKE %s
                        ORDER BY
                          CASE WHEN lower(nom) LIKE lower(%s) THEN 0 ELSE 1 END,
                          nom ASC
                        LIMIT %s
                        """,
                        (f"%{terme}%", f"{terme}%", limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id::text, nom
                        FROM bancarisation.prestataires
                        ORDER BY nom ASC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                return [dict(row) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture prestataires impossible : {exc}",
        ) from exc


@router.get("/prestataires/{prestataire_id}")
def get_prestataire_route(prestataire_id: UUID) -> dict[str, Any]:
    try:
        with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text, nom
                    FROM bancarisation.prestataires
                    WHERE id = %s
                    """,
                    (str(prestataire_id),),
                )
                row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Prestataire introuvable.")
        return dict(row)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture prestataire impossible : {exc}",
        ) from exc
