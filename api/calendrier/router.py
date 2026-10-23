"""API des règles d'échéance (onglet Programme)."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.journal_actions.acteur import acteur_courant

from .recurrence import RecurrenceError
from .service import (
    EcheanceIntrouvable,
    EcheancesFigees,
    apercu,
    apercu_brouillon,
    creer_echeance,
    modifier_echeance,
    programme,
    regenerer_tout,
    supprimer_echeance,
    valider_echeance,
)

router = APIRouter()


class EcheanceWrite(BaseModel):
    libelle: Optional[str] = None
    code_operation: Optional[str] = None
    type_operation: Optional[str] = None
    lib_thema: Optional[str] = None
    ug_ids: Optional[list[str]] = None
    recurrence: Optional[dict[str, Any]] = None
    fenetre_debut: Optional[str] = None
    fenetre_fin: Optional[str] = None
    fenetre_traverse_nouvel_an: Optional[bool] = None
    hypothese: Optional[str] = None
    champs_a_confirmer: Optional[list[str]] = None
    annee_N: Optional[int] = None


class ApercuRequest(BaseModel):
    recurrence: dict[str, Any]
    fenetre_debut: Optional[str] = None
    fenetre_fin: Optional[str] = None
    fenetre_traverse_nouvel_an: bool = False


def _erreur(exc: Exception) -> HTTPException:
    if isinstance(exc, EcheanceIntrouvable):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, EcheancesFigees):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (RecurrenceError, ValueError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/projets/{projet_id}/programme")
def get_programme(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return programme(projet_id)
    except Exception as exc:  # noqa: BLE001
        raise _erreur(exc) from exc


@router.post("/projets/{projet_id}/regenerer")
def post_regenerer(projet_id: UUID, dry_run: bool = False) -> dict[str, Any]:
    try:
        rapports = regenerer_tout(projet_id, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        raise _erreur(exc) from exc
    return {"nb": len(rapports), "rapports": rapports}


@router.post("/projets/{projet_id}/echeances/apercu")
def post_apercu_brouillon(projet_id: UUID, payload: ApercuRequest) -> dict[str, Any]:
    del projet_id
    try:
        return apercu_brouillon(
            payload.recurrence,
            {
                "fenetre_debut": payload.fenetre_debut,
                "fenetre_fin": payload.fenetre_fin,
                "fenetre_traverse_nouvel_an": payload.fenetre_traverse_nouvel_an,
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise _erreur(exc) from exc


@router.post("/projets/{projet_id}/echeances", status_code=status.HTTP_201_CREATED)
def post_echeance(
    projet_id: UUID,
    payload: EcheanceWrite,
    acteur: str | None = Depends(acteur_courant),
) -> dict[str, Any]:
    try:
        return creer_echeance(projet_id, payload.model_dump(exclude_unset=True), acteur=acteur)
    except Exception as exc:  # noqa: BLE001
        raise _erreur(exc) from exc


@router.patch("/echeances/{echeance_id}")
def patch_echeance(
    echeance_id: UUID,
    payload: EcheanceWrite,
    acteur: str | None = Depends(acteur_courant),
) -> dict[str, Any]:
    try:
        return modifier_echeance(
            echeance_id,
            payload.model_dump(exclude_unset=True),
            acteur=acteur,
        )
    except Exception as exc:  # noqa: BLE001
        raise _erreur(exc) from exc


@router.post("/echeances/{echeance_id}/apercu")
def post_apercu(echeance_id: UUID, payload: ApercuRequest) -> dict[str, Any]:
    try:
        return apercu(
            echeance_id,
            payload.recurrence,
            {
                "fenetre_debut": payload.fenetre_debut,
                "fenetre_fin": payload.fenetre_fin,
                "fenetre_traverse_nouvel_an": payload.fenetre_traverse_nouvel_an,
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise _erreur(exc) from exc


@router.post("/echeances/{echeance_id}/valider")
def post_valider(
    echeance_id: UUID,
    acteur: str | None = Depends(acteur_courant),
) -> dict[str, Any]:
    try:
        return valider_echeance(echeance_id, acteur=acteur)
    except Exception as exc:  # noqa: BLE001
        raise _erreur(exc) from exc


@router.delete("/echeances/{echeance_id}")
def delete_echeance(
    echeance_id: UUID,
    forcer: bool = Query(False, description="Détache les occurrences figées puis supprime la règle."),
) -> dict[str, Any]:
    try:
        return supprimer_echeance(echeance_id, forcer=forcer)
    except Exception as exc:  # noqa: BLE001
        raise _erreur(exc) from exc
