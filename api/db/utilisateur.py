"""Connexion métier : identité JWT + rôle authenticated (RLS)."""

from __future__ import annotations

import json
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
    payload = _claims_courants(claims)
    raw = json.dumps(payload)
    conn.execute("select set_config('request.jwt.claims', %s, false)", (raw,))
    conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (payload["sub"],))
    conn.execute("select set_config('request.jwt.claim.role', %s, false)", (payload["role"],))
    conn.execute("set role authenticated")


def connect_utilisateur(
    claims: dict[str, Any] | None = None,
    **kwargs: Any,
) -> psycopg.Connection:
    """Ouverture d'une connexion kererc_backend endossant authenticated."""
    conn = psycopg.connect(get_backend_database_url(), **kwargs)
    try:
        appliquer_identite(conn, claims)
    except Exception:
        conn.close()
        raise
    return conn


def connect_utilisateur_dict(**kwargs: Any) -> psycopg.Connection:
    kwargs.setdefault("row_factory", dict_row)
    return connect_utilisateur(**kwargs)
