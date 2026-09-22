from __future__ import annotations

import asyncio

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from security import IpDenylistMiddleware, _parse_ip_tokens, client_ip, docs_enabled


def _request(*, path: str = "/health", forwarded: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    return Request(scope)


async def _ok(_request: Request) -> Response:
    return JSONResponse({"ok": True})


def test_parse_ip_tokens_ignore_commentaires_et_invalides() -> None:
    raw = "# scan\n136.115.183.53\nnot-an-ip, 1.2.3.4"
    assert _parse_ip_tokens(raw) == {"136.115.183.53", "1.2.3.4"}


def test_docs_enabled_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    assert docs_enabled() is False
    monkeypatch.setenv("ENABLE_DOCS", "1")
    assert docs_enabled() is True


def test_denylist_bloque_x_forwarded_for() -> None:
    mw = IpDenylistMiddleware(app=lambda: None, blocked=frozenset({"136.115.183.53"}))

    allowed = asyncio.run(mw.dispatch(_request(), _ok))
    assert allowed.status_code == 200

    blocked = asyncio.run(mw.dispatch(_request(forwarded="136.115.183.53"), _ok))
    assert blocked.status_code == 403
    assert blocked.body == b'{"detail":"Forbidden"}'


def test_client_ip_prend_la_premiere_du_forwarded() -> None:
    req = _request(forwarded="136.115.183.53, 10.0.0.1")
    assert client_ip(req) == "136.115.183.53"


def test_docs_fermés_sans_enable_docs() -> None:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
