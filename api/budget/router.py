"""Routes budget projet : lecture + ingestion JSON + baseline + mouvements + agrégats."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from .agrege import lister_budget_annuel
from .baseline import router as baseline_router
from .crud import lister_lignes_budget
from .ingestion import BudgetIngestError, ingérer_budget_json
from .mouvements import (
    etiqueter_dernier_mouvement,
    lister_mouvements_occurrence,
    lister_mouvements_projet,
)

router = APIRouter()
router.include_router(baseline_router)

_CHAMPS_MOTIF = frozenset(
    {"montant_ht", "montant_engage", "montant_realise", "statut", "annee"}
)


class MotifPayload(BaseModel):
    champ: str = Field(..., min_length=1)
    motif: str = Field(..., min_length=1)


@router.get("/projets/{projet_id}/budget/lignes")
def list_budget_lignes_route(projet_id: UUID) -> dict[str, Any]:
    try:
        return lister_lignes_budget(projet_id)
    except BudgetIngestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/projets/{projet_id}/budget/annuel")
def list_budget_annuel_route(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return lister_budget_annuel(projet_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture budget annuel impossible : {exc}",
        ) from exc


@router.get("/projets/{projet_id}/budget/mouvements")
def list_budget_mouvements_projet_route(
    projet_id: UUID,
    limite: int = Query(default=500, ge=1, le=2000),
) -> list[dict[str, Any]]:
    try:
        return lister_mouvements_projet(projet_id, limite=limite)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture mouvements impossible : {exc}",
        ) from exc


@router.get("/occurrences/{occurrence_id}/mouvements")
def list_budget_mouvements_occurrence_route(
    occurrence_id: UUID,
    limite: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    try:
        return lister_mouvements_occurrence(occurrence_id, limite=limite)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture mouvements impossible : {exc}",
        ) from exc


@router.patch("/occurrences/{occurrence_id}/dernier-mouvement/motif")
def etiqueter_motif_route(occurrence_id: UUID, payload: MotifPayload) -> dict[str, Any]:
    if payload.champ not in _CHAMPS_MOTIF:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Champ non supporté. Autorisés : {sorted(_CHAMPS_MOTIF)}",
        )
    motif = payload.motif.strip()
    if not motif:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Motif vide.")
    try:
        res = etiqueter_dernier_mouvement(occurrence_id, payload.champ, motif)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Étiquetage motif impossible : {exc}",
        ) from exc
    if res is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun mouvement à étiqueter.")
    return res


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
