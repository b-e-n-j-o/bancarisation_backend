"""Routes bilan de suivi écologique (aperçu, génération, dépôt DREAL, PDF)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from .archiver import archiver_pdf_suivi
from .pdf import nom_fichier, rendre_pdf
from .suivi import (
    SuiviError,
    construire_bilan_suivi,
    deposer_rapport_suivi,
    generer_bilan_suivi,
    lire_rapport_suivi,
    lister_enveloppes_suivi,
    lister_rapports_suivi,
    supprimer_rapport_suivi,
)

router = APIRouter()


class GenererSuiviPayload(BaseModel):
    annee: int = Field(..., ge=1900, le=2200)
    genere_par: str | None = Field(default=None, max_length=200)
    commentaire_synthese: str | None = Field(default=None, max_length=8000)
    commentaires: dict[str, str] | None = Field(
        default=None,
        description="Commentaires BE indexés par occurrence_id",
    )
    deposer: bool = Field(
        default=False,
        description="Si true, pousse aussi vers bilan_suivi (bannette DREAL). Défaut : génération seule.",
    )


@router.get("/projets/{projet_id}/bilans-suivi")
def lister_route(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return lister_rapports_suivi(projet_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture bilans de suivi impossible : {exc}",
        ) from exc


@router.get("/projets/{projet_id}/bilans-suivi/enveloppes")
def lister_enveloppes_route(projet_id: UUID) -> list[dict[str, Any]]:
    """Statuts d'instruction DREAL (dont demandes de complément) — côté BE."""
    try:
        return lister_enveloppes_suivi(projet_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture enveloppes bilan impossible : {exc}",
        ) from exc


@router.get("/projets/{projet_id}/bilans-suivi/apercu")
def apercu_route(
    projet_id: UUID,
    annee: int = Query(..., ge=1900, le=2200),
) -> dict[str, Any]:
    try:
        return construire_bilan_suivi(projet_id, annee)
    except SuiviError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Aperçu bilan de suivi impossible : {exc}",
        ) from exc


@router.get("/projets/{projet_id}/bilans-suivi/apercu.pdf")
def apercu_pdf_route(
    projet_id: UUID,
    annee: int = Query(..., ge=1900, le=2200),
) -> Response:
    try:
        snapshot = construire_bilan_suivi(projet_id, annee)
        bilan = {"donnees": snapshot, "version": 0, "statut": "genere"}
        return Response(
            content=rendre_pdf(bilan),
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="bilan-suivi-apercu.pdf"'},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Aperçu PDF impossible : {exc}",
        ) from exc


@router.post(
    "/projets/{projet_id}/bilans-suivi",
    status_code=status.HTTP_201_CREATED,
)
def generer_route(projet_id: UUID, payload: GenererSuiviPayload) -> dict[str, Any]:
    try:
        return generer_bilan_suivi(
            projet_id,
            payload.annee,
            genere_par=payload.genere_par,
            commentaires=payload.commentaires,
            commentaire_synthese=payload.commentaire_synthese,
            deposer=payload.deposer,
        )
    except SuiviError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Génération bilan de suivi impossible : {exc}",
        ) from exc


@router.get("/bilans-suivi/{rapport_id}")
def lire_route(rapport_id: UUID) -> dict[str, Any]:
    try:
        return lire_rapport_suivi(rapport_id)
    except SuiviError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture impossible : {exc}",
        ) from exc


@router.post("/bilans-suivi/{rapport_id}/deposer")
def deposer_route(
    rapport_id: UUID,
    acteur: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    """Dépose un bilan déjà généré pour instruction DREAL (bannette)."""
    try:
        return deposer_rapport_suivi(rapport_id, acteur=acteur)
    except SuiviError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Dépôt DREAL impossible : {exc}",
        ) from exc


@router.get("/bilans-suivi/{rapport_id}/pdf")
def pdf_route(rapport_id: UUID) -> Response:
    try:
        bilan = lire_rapport_suivi(rapport_id)
    except SuiviError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    try:
        return Response(
            content=rendre_pdf(bilan),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{nom_fichier(bilan)}"'},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rendu PDF impossible : {exc}",
        ) from exc


@router.post("/bilans-suivi/{rapport_id}/pdf/archiver")
def archiver_route(
    rapport_id: UUID,
    remplacer: bool = Query(default=True),
) -> dict[str, Any]:
    try:
        return archiver_pdf_suivi(rapport_id, remplacer=remplacer)
    except SuiviError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Archivage PDF impossible : {exc}",
        ) from exc


@router.delete("/bilans-suivi/{rapport_id}", status_code=status.HTTP_200_OK)
def supprimer_route(
    rapport_id: UUID,
    acteur: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    try:
        return supprimer_rapport_suivi(rapport_id, acteur=acteur)
    except SuiviError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Suppression impossible : {exc}",
        ) from exc
