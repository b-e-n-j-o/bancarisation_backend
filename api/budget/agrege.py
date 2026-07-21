"""Lecture agrégée du budget annuel (profil pluriannuel du projet)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_database_url


def lister_budget_annuel(projet_id: UUID) -> list[dict[str, Any]]:
    """Une ligne par année : initial / prévu / engagé / réalisé + delta.

    S'appuie sur la vue v_budget_delta_annuel (migration 008), qui gère déjà
    le FULL JOIN baseline↔courant (années n'existant que d'un côté).
    """
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    annee,
                    initial::float8              AS initial,
                    prevu::float8                AS prevu,
                    engage::float8               AS engage,
                    realise::float8              AS realise,
                    delta_prevu_initial::float8  AS delta_prevu_initial
                FROM bancarisation.v_budget_delta_annuel
                WHERE projet_id = %s
                ORDER BY annee ASC
                """,
                (str(projet_id),),
            )
            return [dict(r) for r in cur.fetchall()]
