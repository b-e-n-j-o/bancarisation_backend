"""Écriture / lecture du journal d'actions utilisateur."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.db.env import get_database_url


def journaliser(
    *,
    action: str,
    projet_id: UUID | str | None = None,
    cible_type: str | None = None,
    cible_id: str | UUID | None = None,
    detail: dict[str, Any] | None = None,
    acteur: str | None = None,
    cur: psycopg.Cursor | None = None,
) -> None:
    """Enregistre une action dans bancarisation.journal_actions.

    - Ne lève pas d'exception métier si la table est absente (migration pas encore
      jouée) : un savepoint isole l'échec pour ne pas casser la transaction appelante.
    - Passer `cur` pour journaliser dans la même transaction que l'opération.
    """
    if not action or not str(action).strip():
        return

    payload = (
        str(projet_id) if projet_id else None,
        acteur,
        str(action).strip(),
        cible_type,
        str(cible_id) if cible_id is not None else None,
        Jsonb(detail or {}),
    )
    sql = """
        INSERT INTO bancarisation.journal_actions
            (projet_id, acteur, action, cible_type, cible_id, detail)
        VALUES (%s, %s, %s, %s, %s, %s)
    """

    def _run(c: psycopg.Cursor) -> None:
        try:
            c.execute("SAVEPOINT sp_journal_actions")
            c.execute(sql, payload)
            c.execute("RELEASE SAVEPOINT sp_journal_actions")
        except Exception:
            try:
                c.execute("ROLLBACK TO SAVEPOINT sp_journal_actions")
            except Exception:
                pass

    if cur is not None:
        _run(cur)
        return

    with psycopg.connect(get_database_url()) as conn:
        with conn.cursor() as c:
            _run(c)


def lister_actions(
    projet_id: UUID | str,
    *,
    limit: int = 100,
    action: str | None = None,
    cible_type: str | None = None,
) -> list[dict[str, Any]]:
    """Liste les actions d'un projet (plus récentes d'abord)."""
    clauses = ["projet_id = %s"]
    params: list[Any] = [str(projet_id)]
    if action:
        clauses.append("action = %s")
        params.append(action)
    if cible_type:
        clauses.append("cible_type = %s")
        params.append(cible_type)
    params.append(max(1, min(limit, 1000)))

    sql = f"""
        SELECT id::text, projet_id::text, acteur, action,
               cible_type, cible_id, detail, cree_le::text
        FROM bancarisation.journal_actions
        WHERE {' AND '.join(clauses)}
        ORDER BY cree_le DESC
        LIMIT %s
    """
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        if isinstance(row.get("detail"), str):
            import json

            row["detail"] = json.loads(row["detail"])
    return rows
