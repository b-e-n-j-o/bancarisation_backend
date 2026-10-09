from __future__ import annotations

import logging

from fastapi import HTTPException, status
from psycopg import Error as PsycopgError
from psycopg import OperationalError

log = logging.getLogger(__name__)


def _erreur_connexion(exc: BaseException) -> bool:
    if isinstance(exc, OperationalError):
        return True
    nom = type(exc).__name__.lower()
    return "timeout" in nom or "pool" in nom


def http_from_db(exc: BaseException) -> HTTPException:
    """Traduit les erreurs Postgres (RLS) en réponses HTTP."""
    if _erreur_connexion(exc):
        log.error("Connexion Postgres indisponible : %s: %s", type(exc).__name__, exc)
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Base de données indisponible.",
        )

    sqlstate = getattr(exc, "sqlstate", None)
    if isinstance(exc, PsycopgError) and sqlstate is None and exc.diag:
        sqlstate = exc.diag.sqlstate
    if sqlstate == "42501":
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Droits insuffisants.")
    if sqlstate == "40001":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La ressource a changé entre-temps.",
        )
    if sqlstate == "P0002":
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ressource introuvable.")
    if isinstance(exc, PsycopgError):
        log.error("Erreur Postgres non mappée : %s: %s", type(exc).__name__, exc)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Opération refusée.",
    )
