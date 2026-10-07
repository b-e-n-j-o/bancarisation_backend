"""Contexte membre : l'identité vient du JWT (middleware), les droits de la RLS."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from auth.deps import get_claims


@dataclass(frozen=True, slots=True)
class MembreContext:
    user_id: UUID | None
    role: str
    organisation_id: UUID | None
    organisation_nom: str | None = None


def get_membre_context() -> MembreContext:
    claims = get_claims()
    try:
        uid = UUID(str(claims["sub"]))
    except (KeyError, ValueError):
        uid = None
    return MembreContext(
        user_id=uid,
        role=str(claims.get("role") or "authenticated"),
        organisation_id=None,
    )
