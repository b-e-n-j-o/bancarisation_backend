"""Chargement .env et résolution DATABASE_URL pour l'ingestion PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

_OCR_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _OCR_DIR.parents[1]


def load_db_env() -> None:
    """backend/.env puis api/ocr/.env (surcharges locales)."""
    load_dotenv(_BACKEND_DIR / ".env")
    load_dotenv(_OCR_DIR / ".env", override=True)


def get_database_url() -> str:
    load_db_env()
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url

    host = os.environ.get("SUPABASE_HOST", "").strip()
    port = os.environ.get("SUPABASE_PORT", "5432").strip()
    database = os.environ.get("SUPABASE_DATABASE", "postgres").strip()
    user = os.environ.get("SUPABASE_USER", "").strip()
    password = os.environ.get("SUPABASE_PASSWORD", "").strip()

    if not all([host, user, password]):
        raise RuntimeError(
            "DATABASE_URL absente. Définir DATABASE_URL ou "
            "SUPABASE_HOST / SUPABASE_USER / SUPABASE_PASSWORD dans backend/.env"
        )

    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}"
    )
