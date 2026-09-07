"""Lecture du journal projet (mouvements + actions métier)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from .service import lister_journal_projet

router = APIRouter()

_FAMILLES = frozenset({"periode", "statut", "budget", "evenement"})


@router.get("/projets/{projet_id}/journal")
def journal_projet_route(
    projet_id: UUID,
    limite: int = Query(default=400, ge=1, le=2000),
    famille: str | None = Query(
        default=None,
        description="periode | statut | budget | evenement",
    ),
) -> list[dict[str, Any]]:
    if famille and famille not in _FAMILLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"famille inconnue (attendues : {sorted(_FAMILLES)}).",
        )
    try:
        return lister_journal_projet(projet_id, limite=limite, famille=famille)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture du journal impossible : {exc}",
        ) from exc
