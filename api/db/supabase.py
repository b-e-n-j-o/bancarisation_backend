"""Client Supabase REST (HTTPS) — même accès que le frontend / crud_projet."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

_API_DIR = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _API_DIR.parent
_OCR_DIR = _API_DIR / "ocr"


def load_supabase_env() -> None:
    load_dotenv(_BACKEND_DIR / ".env")
    load_dotenv(_OCR_DIR / ".env", override=True)


def get_supabase() -> Client:
    load_supabase_env()
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY requis dans backend/.env"
        )
    return create_client(url, key)


def bancarisation(client: Client):
    return client.schema("bancarisation")
