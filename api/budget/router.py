"""Routes budget projet : lecture + ingestion JSON."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from .crud import lister_lignes_budget
from .ingestion import BudgetIngestError, ingérer_budget_json

router = APIRouter()


@router.get("/projets/{projet_id}/budget/lignes")
def list_budget_lignes_route(projet_id: UUID) -> dict[str, Any]:
    try:
        return lister_lignes_budget(projet_id)
    except BudgetIngestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/projets/{projet_id}/budget/ingest",
    status_code=status.HTTP_201_CREATED,
)
async def ingest_budget_route(
    projet_id: UUID,
    file: UploadFile = File(...),
    replace: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        content = await file.read()
        if not content:
            raise BudgetIngestError("Fichier vide.")
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, dict):
            raise BudgetIngestError("Le JSON doit être un objet.")
        return ingérer_budget_json(
            projet_id=projet_id,
            payload=payload,
            fichier_nom=file.filename or "lignes_budget.json",
            replace=replace,
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="JSON invalide.",
        ) from exc
    except BudgetIngestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
