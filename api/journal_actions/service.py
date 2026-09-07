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


_CHAMPS_PERIODE = frozenset({"annee", "mois_debut", "mois_fin"})
_CHAMPS_BUDGET = frozenset(
    {"montant_ht", "montant_engage", "montant_realise", "montant_ttc"}
)


def _famille_mouvement(champ: str) -> str:
    if champ in _CHAMPS_PERIODE:
        return "periode"
    if champ == "statut":
        return "statut"
    if champ in _CHAMPS_BUDGET:
        return "budget"
    return "budget"


def _evt_mouvement(m: dict[str, Any]) -> dict[str, Any]:
    champ = str(m.get("champ") or "")
    acteur = (m.get("modifie_par") or "").strip() or None
    return {
        "id": f"mvt:{m.get('id')}",
        "source": "mouvement",
        "at": m.get("modifie_le"),
        "acteur": acteur,
        "famille": _famille_mouvement(champ),
        "champ": champ,
        "ancienne_val": m.get("ancienne_val"),
        "nouvelle_val": m.get("nouvelle_val"),
        "motif": m.get("motif") or None,
        "occurrence_id": m.get("occurrence_id"),
        "occurrence_code": m.get("occurrence_code"),
        "occurrence_titre": m.get("occurrence_titre"),
        "occurrence_annee": m.get("occurrence_annee"),
        "cible_type": "occurrence",
        "cible_id": m.get("occurrence_id"),
        "detail": None,
    }


def _evt_action(a: dict[str, Any]) -> dict[str, Any]:
    acteur = (a.get("acteur") or "").strip() or None
    cible_type = a.get("cible_type")
    cible_id = a.get("cible_id")
    occ_id = cible_id if cible_type == "occurrence" else None
    return {
        "id": f"act:{a.get('id')}",
        "source": "action",
        "at": a.get("cree_le"),
        "acteur": acteur,
        "famille": "evenement",
        "champ": a.get("action"),
        "ancienne_val": None,
        "nouvelle_val": None,
        "motif": None,
        "occurrence_id": occ_id,
        "occurrence_code": None,
        "occurrence_titre": None,
        "occurrence_annee": None,
        "cible_type": cible_type,
        "cible_id": cible_id,
        "detail": a.get("detail") or {},
    }


def lister_journal_projet(
    projet_id: UUID | str,
    *,
    limite: int = 400,
    famille: str | None = None,
) -> list[dict[str, Any]]:
    """Timeline projet : mouvements d'occurrences + actions métier.

    Même matière que le §4 du PDF de bilan, à l'échelle de tout le projet
    (toutes années), plus les événements `journal_actions` (génération de
    bilan, etc.). `acteur` est NULL tant que l'auth n'est pas branchée.
    """
    from api.budget.mouvements import lister_mouvements_projet

    cap = max(1, min(limite, 2000))
    events: list[dict[str, Any]] = [
        _evt_mouvement(m) for m in lister_mouvements_projet(projet_id, limite=cap)
    ]
    try:
        events.extend(_evt_action(a) for a in lister_actions(projet_id, limit=cap))
    except Exception:
        # Table absente (migration 015 pas encore jouée) : on garde les mouvements.
        pass

    if famille:
        events = [e for e in events if e.get("famille") == famille]

    events.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    return events[:cap]
