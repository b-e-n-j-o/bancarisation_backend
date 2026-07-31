"""OAuth client_credentials Sentinel Hub — token mis en cache (~1 h)."""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import httpx

TOKEN_URL = (
    "https://services.sentinel-hub.com/auth/realms/main"
    "/protocol/openid-connect/token"
)

# Renouveler un peu avant l'expiration réelle.
_EXPIRY_MARGIN_S = 60

_lock = threading.Lock()
_cached_token: Optional[str] = None
_expires_at: float = 0.0


class SentinelAuthError(Exception):
    pass


def _credentials() -> tuple[str, str]:
    client_id = (os.getenv("SENTINEL_CLIENT_ID") or os.getenv("CLIENT_ID") or "").strip()
    client_secret = (
        os.getenv("SENTINEL_CLIENT_SECRET") or os.getenv("CLIENT_SECRET") or ""
    ).strip()
    if not client_id or not client_secret:
        raise SentinelAuthError(
            "SENTINEL_CLIENT_ID / SENTINEL_CLIENT_SECRET absents du .env."
        )
    return client_id, client_secret


def get_sentinel_token(*, force_refresh: bool = False) -> str:
    """Retourne un access_token valide (cache process-local)."""
    global _cached_token, _expires_at

    with _lock:
        now = time.monotonic()
        if (
            not force_refresh
            and _cached_token
            and now < _expires_at - _EXPIRY_MARGIN_S
        ):
            return _cached_token

        client_id, client_secret = _credentials()
        try:
            resp = httpx.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise SentinelAuthError(f"Échec réseau auth Sentinel Hub: {exc}") from exc

        if resp.status_code != 200:
            raise SentinelAuthError(
                f"Auth Sentinel Hub {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise SentinelAuthError("Réponse auth sans access_token.")

        expires_in = int(data.get("expires_in") or 3600)
        _cached_token = token
        _expires_at = time.monotonic() + expires_in
        return token


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_sentinel_token()}"}
