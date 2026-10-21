"""Lectures JSON-compatibles sous la connexion déjà ouverte."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql


def lignes(conn: psycopg.Connection, requete: str, params: tuple | list | None = None) -> list[dict[str, Any]]:
    curseur = conn.execute(requete, params or ())
    return [dict(ligne) for ligne in curseur.fetchall()]


def une(conn: psycopg.Connection, requete: str, params: tuple | list | None = None) -> dict[str, Any] | None:
    trouvees = lignes(conn, requete, params)
    return trouvees[0] if trouvees else None


def colonnes_table(conn: psycopg.Connection, table: str) -> list[dict[str, str]]:
    return [
        {"nom": r["column_name"], "type": r["udt_name"], "data_type": r["data_type"]}
        for r in lignes(
            conn,
            """
            select column_name, udt_name, data_type
              from information_schema.columns
             where table_schema = 'bancarisation' and table_name = %s
             order by ordinal_position
            """,
            (table,),
        )
    ]


def table_existe(conn: psycopg.Connection, table: str) -> bool:
    row = une(
        conn,
        """
        select 1 as ok from information_schema.tables
         where table_schema = 'bancarisation' and table_name = %s
        """,
        (table,),
    )
    return row is not None


def pour_json(valeur: Any) -> Any:
    if isinstance(valeur, dict):
        return {str(k): pour_json(v) for k, v in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [pour_json(v) for v in valeur]
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, UUID):
        return str(valeur)
    if isinstance(valeur, datetime):
        return valeur.isoformat()
    if isinstance(valeur, date):
        return valeur.isoformat()
    if isinstance(valeur, (bytes, memoryview)):
        return None
    return valeur


def ecrire_json(chemin, payload: Any) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        json.dumps(pour_json(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def annee_clause(alias: str, annee_min: int | None, annee_max: int | None) -> tuple[str, list[Any]]:
    morceaux: list[str] = []
    params: list[Any] = []
    if annee_min is not None:
        morceaux.append(f"{alias}.annee >= %s")
        params.append(annee_min)
    if annee_max is not None:
        morceaux.append(f"{alias}.annee <= %s")
        params.append(annee_max)
    if not morceaux:
        return "", []
    return " and " + " and ".join(morceaux), params


def ident(nom: str) -> sql.Identifier:
    return sql.Identifier(nom)
