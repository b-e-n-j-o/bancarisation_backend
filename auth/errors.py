from __future__ import annotations

from fastapi import HTTPException, status
from psycopg import Error as PsycopgError


def http_from_db(exc: BaseException) -> HTTPException:
    """Traduit les erreurs Postgres (RLS) en réponses HTTP."""
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
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Opération refusée.",
    )
