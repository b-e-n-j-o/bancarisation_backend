"""
router.py — Endpoints HTTP pour les données OCR post-ingestion.

Expose api.ocr.db.crud sous /api/projets/{id}/…
"""

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from .db import crud
from .analyse_jobs import lire_status
from .analyse_service import lancer_analyse

router = APIRouter()


class ActionFicheCreateRequest(BaseModel):
    code: str
    categorie: str
    titre: str
    contenu_integral: str
    cle: Optional[str] = None
    ug_ids: list[str] = Field(default_factory=list)
    lib_thema: Optional[str] = "autre"


class ActionFicheUpdateRequest(BaseModel):
    ug_ids: Optional[list[str]] = None
    titre: Optional[str] = None
    contenu_integral: Optional[str] = None
    categorie: Optional[str] = None
    lib_thema: Optional[str] = None


class OccurrenceCreateRequest(BaseModel):
    annee: int
    code: str
    titre: str
    categorie: str
    lib_thema: Optional[str] = "autre"
    statut: str = "planifie"
    ug_ids: list[str] = Field(default_factory=list)
    mois_debut: Optional[int] = None
    mois_fin: Optional[int] = None
    traverse_nouvel_an: bool = False
    echeance_id: Optional[UUID] = None
    commentaire: Optional[str] = None


class OccurrenceUpdateRequest(BaseModel):
    annee: Optional[int] = None
    code: Optional[str] = None
    titre: Optional[str] = None
    categorie: Optional[str] = None
    lib_thema: Optional[str] = None
    statut: Optional[str] = None
    ug_ids: Optional[list[str]] = None
    mois_debut: Optional[int] = None
    mois_fin: Optional[int] = None
    traverse_nouvel_an: Optional[bool] = None
    date_realisation: Optional[str] = None
    commentaire: Optional[str] = None


def _db_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _server_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/projets/{projet_id}/metadata")
def get_metadata(projet_id: UUID) -> dict[str, Any] | None:
    try:
        return crud.get_metadata(projet_id)
    except RuntimeError as exc:
        raise _server_error(exc) from exc


@router.get("/projets/{projet_id}/actions-fiche")
def list_actions_fiche(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return crud.lister_actions(projet_id)
    except RuntimeError as exc:
        raise _server_error(exc) from exc


@router.get("/projets/{projet_id}/unites-gestion/{ug_id}/actions")
def list_actions_pour_ug(projet_id: UUID, ug_id: str) -> list[dict[str, Any]]:
    try:
        return crud.lister_actions_pour_ug(projet_id, ug_id)
    except ValueError as exc:
        raise _db_error(exc) from exc
    except RuntimeError as exc:
        raise _server_error(exc) from exc


@router.post("/projets/{projet_id}/actions-fiche", status_code=status.HTTP_201_CREATED)
def create_action_fiche(projet_id: UUID, payload: ActionFicheCreateRequest) -> dict[str, Any]:
    try:
        return crud.creer_action_fiche(projet_id, **payload.model_dump(exclude_none=True))
    except (RuntimeError, ValueError) as exc:
        raise _db_error(exc) from exc


@router.patch("/projets/{projet_id}/actions-fiche/{action_id}")
def update_action_fiche(
    projet_id: UUID,
    action_id: UUID,
    payload: ActionFicheUpdateRequest,
) -> dict[str, Any]:
    champs = payload.model_dump(exclude_none=True)
    if not champs:
        raise HTTPException(status_code=400, detail="Aucun champ à modifier.")
    try:
        row = crud.modifier_action_fiche(projet_id, action_id, **champs)
    except (RuntimeError, ValueError) as exc:
        raise _db_error(exc) from exc
    if not row:
        raise HTTPException(status_code=404, detail="Fiche-action introuvable.")
    return row


@router.get("/projets/{projet_id}/echeances")
def list_echeances(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return crud.lister_echeances(projet_id)
    except RuntimeError as exc:
        raise _server_error(exc) from exc


@router.get("/projets/{projet_id}/echeances/a-revoir")
def list_echeances_a_revoir(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return crud.echeances_a_revoir(projet_id)
    except RuntimeError as exc:
        raise _server_error(exc) from exc


@router.get("/projets/{projet_id}/echeances/non-placables")
def list_echeances_non_placables(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return crud.echeances_non_placables(projet_id)
    except RuntimeError as exc:
        raise _server_error(exc) from exc


@router.get("/projets/{projet_id}/occurrences")
def list_occurrences(
    projet_id: UUID,
    annee: Optional[int] = None,
    ug_id: Optional[str] = None,
    inclure_supprimees: bool = True,
) -> list[dict[str, Any]]:
    try:
        return crud.lister_occurrences(
            projet_id,
            annee=annee,
            ug_id=ug_id,
            inclure_supprimees=inclure_supprimees,
        )
    except RuntimeError as exc:
        raise _server_error(exc) from exc


@router.post("/projets/{projet_id}/occurrences", status_code=status.HTTP_201_CREATED)
def create_occurrence(projet_id: UUID, payload: OccurrenceCreateRequest) -> dict[str, Any]:
    try:
        return crud.creer_occurrence(projet_id, **payload.model_dump(exclude_none=True))
    except (RuntimeError, ValueError) as exc:
        raise _db_error(exc) from exc


@router.patch("/occurrences/{occurrence_id}")
def update_occurrence(occurrence_id: UUID, payload: OccurrenceUpdateRequest) -> dict[str, Any]:
    champs = payload.model_dump(exclude_none=True)
    if not champs:
        raise HTTPException(status_code=400, detail="Aucun champ à modifier.")
    try:
        row = crud.modifier_occurrence(occurrence_id, **champs)
    except (RuntimeError, ValueError) as exc:
        raise _db_error(exc) from exc
    if not row:
        raise HTTPException(status_code=404, detail="Occurrence introuvable.")
    return row


@router.delete(
    "/occurrences/{occurrence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_occurrence(occurrence_id: UUID, definitif: bool = False) -> Response:
    try:
        ok = crud.supprimer_occurrence(occurrence_id, definitif=definitif)
    except RuntimeError as exc:
        raise _server_error(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Occurrence introuvable.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projets/{projet_id}/analyse-status")
def analyse_status(projet_id: UUID) -> dict[str, Any]:
    return lire_status(str(projet_id))


@router.post("/projets/{projet_id}/analyse-pdf", status_code=status.HTTP_202_ACCEPTED)
async def start_analyse_pdf(
    projet_id: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    replace: bool = True,
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")

    status_actuel = lire_status(str(projet_id))
    if status_actuel.get("status") == "running":
        raise HTTPException(status_code=409, detail="Une analyse est déjà en cours.")

    contenu = await file.read()
    if len(contenu) < 100:
        raise HTTPException(status_code=400, detail="Fichier PDF vide ou invalide.")

    background_tasks.add_task(
        lancer_analyse,
        str(projet_id),
        contenu,
        file.filename,
        replace=replace,
    )
    return {
        "status": "started",
        "projet_id": str(projet_id),
        "fichier": file.filename,
        "message": "Analyse lancée en arrière-plan (OCR → extraction → base).",
    }
