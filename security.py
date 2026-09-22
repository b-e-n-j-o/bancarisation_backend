"""Contrôles d'exposition OpenAPI et denylist d'adresses IP."""

from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("security")

BLOCKED_IPS_FILE = Path(__file__).resolve().parent / "blocked_ips.txt"


def docs_enabled() -> bool:
    """Swagger / ReDoc / openapi.json : opt-in uniquement (fermé en prod)."""
    return os.getenv("ENABLE_DOCS", "").strip().lower() in {"1", "true", "yes"}


def _parse_ip_tokens(raw: str) -> set[str]:
    tokens: set[str] = set()
    for chunk in raw.replace("\n", ",").split(","):
        token = chunk.strip()
        if not token or token.startswith("#"):
            continue
        try:
            ipaddress.ip_address(token)
        except ValueError:
            logger.warning("IP denylist : entrée ignorée (invalide) : %s", token)
            continue
        tokens.add(token)
    return tokens


def load_blocked_ips(extra_file: Path | None = BLOCKED_IPS_FILE) -> frozenset[str]:
    ips = _parse_ip_tokens(os.getenv("BLOCKED_IPS", ""))
    path = extra_file if extra_file is not None else BLOCKED_IPS_FILE
    if path.is_file():
        ips.update(_parse_ip_tokens(path.read_text(encoding="utf-8")))
    return frozenset(ips)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


class IpDenylistMiddleware(BaseHTTPMiddleware):
    """Bloque les IPs listées dans BLOCKED_IPS et backend/blocked_ips.txt."""

    def __init__(self, app, blocked: frozenset[str] | None = None) -> None:
        super().__init__(app)
        self.blocked = blocked if blocked is not None else load_blocked_ips()

    async def dispatch(self, request: Request, call_next) -> Response:
        ip = client_ip(request)
        if ip and ip in self.blocked:
            logger.warning("IP bloquée (denylist) : %s %s %s", ip, request.method, request.url.path)
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        return await call_next(request)
