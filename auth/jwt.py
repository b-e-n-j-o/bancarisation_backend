from __future__ import annotations

import logging
import os
from typing import Any

import jwt
from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

_jwks_client: jwt.PyJWKClient | None = None


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


def _cle_verification(token: str) -> tuple[Any, list[str]]:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")
    if alg == "HS256":
        return jwt_secret(), ["HS256"]
    if alg in ("ES256", "RS256"):
        global _jwks_client
        if _jwks_client is None:
            base = os.getenv("SUPABASE_URL", "https://supabase-dev.kerelia.fr").rstrip("/")
            _jwks_client = jwt.PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json")
        return _jwks_client.get_signing_key_from_jwt(token).key, [alg]
    raise jwt.InvalidTokenError(f"algorithme non accepté: {alg}")


def verifier_jwt(token: str) -> dict[str, Any]:
    try:
        key, algorithms = _cle_verification(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        logger.warning("JWT rejeté: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton expiré.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.PyJWTError as exc:
        logger.warning("JWT rejeté: %s", type(exc).__name__)
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
