from __future__ import annotations

import os
from typing import Any

import jwt
from fastapi import HTTPException, Request, status


def jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "").strip() or os.getenv("SUPABASE_JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET (ou SUPABASE_JWT_SECRET) manquant dans backend/.env")
    return secret


def extraire_bearer(request: Request) -> str:
    header = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton d'authentification manquant.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = header[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton d'authentification manquant.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def verifier_jwt(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            jwt_secret(),
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton expiré.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton invalide.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if not claims.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton sans identifiant utilisateur.",
        )
    claims.setdefault("role", "authenticated")
    return claims
