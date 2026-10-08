"""Connexion métier : identité JWT + rôle authenticated (RLS)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_backend_database_url
from auth.deps import jwt_claims


def _claims_courants(claims: dict[str, Any] | None = None) -> dict[str, Any]:
    data = claims if claims is not None else jwt_claims.get()
    if not data:
        raise RuntimeError(
            "Aucune identité utilisateur : la requête doit porter un JWT vérifié."
        )
    payload = {
        "sub": str(data["sub"]),
        "role": data.get("role") or "authenticated",
    }
    if data.get("aal"):
        payload["aal"] = data["aal"]
    if data.get("email"):
        payload["email"] = data["email"]
    return payload


def appliquer_identite(conn: psycopg.Connection, claims: dict[str, Any] | None = None) -> None:
    """Pose l'identité pour la transaction en cours. À appeler dedans, avant la requête."""
    payload = _claims_courants(claims)
    raw = json.dumps(payload)
    conn.execute("select set_config('request.jwt.claims', %s, true)", (raw,))
    conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (payload["sub"],))
    conn.execute("select set_config('request.jwt.claim.role', %s, true)", (payload["role"],))
    conn.execute("set local role authenticated")


@contextmanager
def connect_utilisateur(
    claims: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Iterator[psycopg.Connection]:
    """kererc_backend, puis authenticated et le JWT, le temps d'une transaction."""
    conn = psycopg.connect(get_backend_database_url(), **kwargs)
    try:
        with conn.transaction():
            appliquer_identite(conn, claims)
            yield conn
    finally:
        conn.close()


@contextmanager
def connect_utilisateur_dict(**kwargs: Any) -> Iterator[psycopg.Connection]:
    kwargs.setdefault("row_factory", dict_row)
    with connect_utilisateur(**kwargs) as conn:
        yield conn
