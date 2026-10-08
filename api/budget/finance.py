"""Écritures des montants d'occurrence dans occurrence_finance (055)."""

from __future__ import annotations

from typing import Any

import psycopg

CHAMPS_FINANCE = (
    "montant_ht",
    "montant_engage",
    "montant_realise",
    "montant_initial",
    "annee_initiale",
)


def separer_finance(champs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retire les montants du dict occurrence. Ils vivent dans occurrence_finance."""
    finance = {k: champs[k] for k in CHAMPS_FINANCE if k in champs}
    reste = {k: v for k, v in champs.items() if k not in finance}
    return reste, finance


def ecrire_finance(cur: psycopg.Cursor, occurrence_id: str, finance: dict[str, Any]) -> None:
    """insert … on conflict do update. Le trigger recopie projet_id."""
    if not finance:
        return
    cols = list(finance)
    valeurs = ", ".join(["%s"] * (1 + len(cols)))
    maj = ", ".join(f"{c} = excluded.{c}" for c in cols)
    cur.execute(
        f"""
        insert into bancarisation.occurrence_finance (occurrence_id, {", ".join(cols)})
        values ({valeurs})
        on conflict (occurrence_id) do update set {maj}
        """,
        [occurrence_id, *[finance[c] for c in cols]],
    )


def lire_finance(cur: psycopg.Cursor, occurrence_id: str) -> dict[str, Any]:
    cur.execute(
        """
        select montant_ht, montant_engage, montant_realise, montant_initial, annee_initiale
          from bancarisation.occurrence_finance
         where occurrence_id = %s
        """,
        (occurrence_id,),
    )
    row = cur.fetchone()
    if not row:
        return {cle: None for cle in CHAMPS_FINANCE}
    return {cle: row[cle] for cle in CHAMPS_FINANCE}


def monter_finance(ligne: dict[str, Any] | None, finance: dict[str, Any]) -> dict[str, Any] | None:
    """Remplace les anciennes colonnes d'occurrence par la ligne protégée."""
    if ligne is None:
        return None
    out = dict(ligne)
    out.update(finance)
    return out
