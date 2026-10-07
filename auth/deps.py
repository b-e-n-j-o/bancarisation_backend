from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from auth.jwt import extraire_bearer, verifier_jwt

jwt_claims: ContextVar[dict[str, Any] | None] = ContextVar("jwt_claims", default=None)

_PUBLIC_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


def get_claims() -> dict[str, Any]:
    claims = jwt_claims.get()
    if not claims:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton d'authentification manquant.",
        )
    return claims


class JwtAuthMiddleware(BaseHTTPMiddleware):
    """Vérifie le Bearer JWT sur toutes les routes sauf santé / docs."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or path in _PUBLIC_PATHS:
            return await call_next(request)
        try:
            token = extraire_bearer(request)
            claims = verifier_jwt(token)
        except Exception as exc:
            from fastapi import HTTPException

            if isinstance(exc, HTTPException):
                return JSONResponse(
                    {"detail": exc.detail},
                    status_code=exc.status_code,
                    headers=dict(exc.headers or {}),
                )
            raise
        token_ctx = jwt_claims.set(claims)
        try:
            return await call_next(request)
        finally:
            jwt_claims.reset(token_ctx)
