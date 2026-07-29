"""Routes API couche contrôle DREAL."""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from api.controle import crud
from api.controle.schemas import (
    ActeDrealOut,
    ActionFicheOption,
    ArreteOut,
    BilanSuiviOut,
    DossierControleOut,
    GenererActeBody,
    ItemBannetteOut,
    PatchBilanBody,
    StatutProjetOut,
)
from api.documents.crud_document import DocumentServiceError, upload_document
from api.ocr.arrete import service as arrete_service
from api.parc.deps import MembreContext, get_membre_context

logger = logging.getLogger("controle.router")

router = APIRouter(prefix="/parc/controle")


@router.get("/bannette", response_model=list[ItemBannetteOut])
def get_bannette(
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> list[ItemBannetteOut]:
    try:
        return crud.lister_bannette(
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bannette impossible : {exc}",
        ) from exc


@router.get("/statuts", response_model=list[StatutProjetOut])
def get_statuts(
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> list[StatutProjetOut]:
    try:
        rows = crud.lister_statuts(
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
        return [
            StatutProjetOut(
                projet_id=r["projet_id"],
                statut_controle=r["statut_controle"],
                force_manuel=bool(r.get("force_manuel")),
            )
            for r in rows
        ]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Statuts contrôle impossibles : {exc}",
        ) from exc


@router.get("/projets/{projet_id}/dossier", response_model=DossierControleOut)
def get_dossier(
    projet_id: UUID,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> DossierControleOut:
    try:
        return crud.lire_dossier(
            projet_id,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Dossier contrôle impossible : {exc}",
        ) from exc


@router.get("/projets/{projet_id}/actions-fiche", response_model=list[ActionFicheOption])
def get_actions_fiche(
    projet_id: UUID,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> list[ActionFicheOption]:
    try:
        return crud.lister_actions_fiche(
            projet_id,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.patch(
    "/projets/{projet_id}/bilans/{bilan_id}",
    response_model=BilanSuiviOut,
)
def patch_bilan(
    projet_id: UUID,
    bilan_id: UUID,
    body: PatchBilanBody,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> BilanSuiviOut:
    try:
        return crud.patch_bilan_statut(
            projet_id,
            bilan_id,
            body.statut,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post(
    "/projets/{projet_id}/actes/{acte_id}/emettre",
    response_model=ActeDrealOut,
)
def post_emettre_acte(
    projet_id: UUID,
    acte_id: UUID,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> ActeDrealOut:
    try:
        return crud.emettre_acte(
            projet_id,
            acte_id,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post(
    "/projets/{projet_id}/actes/generer",
    response_model=ActeDrealOut,
    status_code=status.HTTP_201_CREATED,
)
def post_generer_acte(
    projet_id: UUID,
    body: GenererActeBody,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> ActeDrealOut:
    try:
        return crud.generer_acte_depuis_ecarts(
            projet_id,
            type_acte=body.type,
            delai_jours=body.delai_jours,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post(
    "/projets/{projet_id}/arretes",
    response_model=ArreteOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_arrete(
    projet_id: UUID,
    membre: Annotated[MembreContext, Depends(get_membre_context)],
    type: str = Form(...),
    reference: str | None = Form(default=None),
    date_notification: date | None = Form(default=None),
    action_fiche_ids: str | None = Form(
        default=None,
        description="UUIDs action_fiche séparés par des virgules",
    ),
    file: UploadFile = File(..., description="PDF de l'arrêté (obligatoire)"),
) -> ArreteOut:
    """Upload PDF dans `documents-projet/{projet_id}/arrete/{ts}_arrete.pdf`
    + création de la ligne `arrete` liée via `document_id`.
    """
    allowed = {
        "declaration_loi_eau",
        "autorisation_env",
        "derogation_ep",
        "arrete_modificatif",
        "autre",
    }
    if type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"type invalide (attendu : {', '.join(sorted(allowed))})",
        )

    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fichier PDF obligatoire",
        )
    lower = filename.lower()
    ctype = (file.content_type or "").lower()
    if not (lower.endswith(".pdf") or "pdf" in ctype):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seul un PDF est accepté pour l'arrêté",
        )

    try:
        content = await file.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Fichier vide",
            )
        # Même bucket / pattern que les bilans :
        # documents-projet/{projet_uuid}/arrete/{timestamp}_arrete.pdf
        doc = upload_document(
            projet_id=projet_id,
            file_name="arrete.pdf",
            content=content,
            content_type="application/pdf",
            categorie="arrete",
            date_document=date_notification,
            description=f"Arrêté {reference or type}",
            nom=reference or "Arrêté",
            sous_dossier="arrete",
        )
        document_id = UUID(str(doc["id"]))
    except DocumentServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    af_ids: list[UUID] = []
    if action_fiche_ids and action_fiche_ids.strip():
        for part in action_fiche_ids.split(","):
            part = part.strip()
            if part:
                af_ids.append(UUID(part))

    try:
        out = crud.creer_arrete(
            projet_id,
            type_arrete=type,
            reference=reference,
            date_notification=date_notification,
            document_id=document_id,
            action_fiche_ids=af_ids,
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Création arrêté impossible : {exc}",
        ) from exc

    # OCR + extraction LLM (peut être long). Si échec, l'arrêté + PDF restent.
    try:
        out = arrete_service.extraire_et_persister(
            pdf_bytes=content,
            arrete_id=out.id,
            projet_id=projet_id,
            document_id=document_id,
            type_form=type,
            reference_form=reference,
            date_notification_form=date_notification,
            filename=filename,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Extraction IA échouée pour arrêté %s (PDF conservé)",
            out.id,
        )
        # On renvoie quand même l'arrêté créé ; l'utilisateur pourra relancer
        # via POST /api/ocr/arrete/projets/.../extraire
        _ = exc

    return out
