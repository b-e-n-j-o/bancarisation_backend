from __future__ import annotations

import time

import jwt
import pytest
from fastapi import HTTPException

from auth.errors import http_from_db
from auth.jwt import verifier_jwt


def test_verifier_jwt_ok(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    token = jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "aud": "authenticated",
            "role": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        "test-secret",
        algorithm="HS256",
    )
    claims = verifier_jwt(token)
    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"


def test_verifier_jwt_invalide(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    with pytest.raises(HTTPException) as exc:
        verifier_jwt("pas-un-jwt")
    assert exc.value.status_code == 401


def test_http_from_db_42501() -> None:
    class Fake(Exception):
        sqlstate = "42501"

    mapped = http_from_db(Fake())
    assert mapped.status_code == 403


def test_http_from_db_connexion() -> None:
    from psycopg import OperationalError

    mapped = http_from_db(OperationalError("connection timeout"))
    assert mapped.status_code == 503


def test_http_from_db_40001() -> None:
    class Fake(Exception):
        sqlstate = "40001"

    mapped = http_from_db(Fake())
    assert mapped.status_code == 409
