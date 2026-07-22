"""Contexte membre résolu depuis le header X-User-Id (V1, sans JWT)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg
from fastapi import Header, HTTPException, status
from psycopg.rows import dict_row

from api.db.env import get_database_url

# Défaut démo : contrôleur du parc entier (sans header).
_DEFAULT_ROLE = "controleur"
_DEFAULT_USER_ID = UUID("c1000000-0000-0000-0000-000000000001")


@dataclass(frozen=True, slots=True)
class MembreContext:
    user_id: UUID | None
    role: str
    organisation_id: UUID | None


def _lookup_membre(user_id: UUID) -> MembreContext | None:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, organisation_id, role
                FROM bancarisation.membre
                WHERE user_id = %s AND actif
                """,
                (str(user_id),),
            )
            row = cur.fetchone()
    if not row:
        return None
    org = row["organisation_id"]
    return MembreContext(
        user_id=row["user_id"],
        role=row["role"],
        organisation_id=UUID(str(org)) if org else None,
    )


def get_membre_context(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> MembreContext:
    """Résout le membre courant.

    - Pas de header → contrôleur démo (voit tout le parc).
    - Header présent → lookup `bancarisation.membre`, 403 si inconnu/inactif.
    """
    if not x_user_id or not x_user_id.strip():
        return MembreContext(
            user_id=_DEFAULT_USER_ID,
            role=_DEFAULT_ROLE,
            organisation_id=None,
        )
    try:
        uid = UUID(x_user_id.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Id invalide (uuid attendu).",
        ) from exc

    membre = _lookup_membre(uid)
    if membre is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Utilisateur inconnu ou inactif dans bancarisation.membre.",
        )
    return membre
