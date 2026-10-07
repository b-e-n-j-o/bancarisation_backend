"""Chargement .env et résolution DATABASE_URL pour l'ingestion PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

_API_DIR = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _API_DIR.parent


def load_db_env() -> None:
    """Charge backend/.env (idempotent). Cherche aussi le cwd si besoin."""
    candidates = [
        _BACKEND_DIR / ".env",
        Path.cwd() / ".env",
        Path.cwd() / "backend" / ".env",
    ]
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)


def get_backend_database_url() -> str:
    """Connexion du rôle kererc_backend (NOINHERIT) pour les requêtes métier RLS."""
    load_db_env()
    url = os.environ.get("DATABASE_URL_BACKEND", "").strip()
    if url:
        return url
    raise RuntimeError(
        "DATABASE_URL_BACKEND manquant : le backend métier doit se connecter "
        "avec le rôle kererc_backend (voir db/031_role_kererc_backend.sql)."
    )


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

    missing = [
        name
        for name, val in (
            ("SUPABASE_HOST", host),
            ("SUPABASE_USER", user),
            ("SUPABASE_PASSWORD", password),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "Connexion Postgres impossible : variables manquantes "
            f"({', '.join(missing)}). "
            "Définir DATABASE_URL ou SUPABASE_HOST / SUPABASE_USER / "
            "SUPABASE_PASSWORD dans backend/.env (et redémarrer uvicorn)."
        )

    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}"
    )
