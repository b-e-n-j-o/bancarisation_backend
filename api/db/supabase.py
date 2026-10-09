"""Client Supabase : storage et Auth Admin uniquement (service_role)."""

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


def get_supabase_admin() -> Client:
    """Contourne la RLS : réservé au storage, à Auth Admin et aux jobs système."""
    load_supabase_env()
    url = os.getenv("SUPABASE_URL", "").strip()
    key = (
        os.getenv("SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL et SERVICE_ROLE_KEY (ou SUPABASE_SERVICE_ROLE_KEY) requis dans backend/.env"
        )
    return create_client(url, key)


def get_supabase() -> Client:
    """Alias historique : ne plus utiliser pour le CRUD métier."""
    return get_supabase_admin()


def bancarisation(client: Client):
    return client.schema("bancarisation")
