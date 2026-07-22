"""Routes API vue de contrôle du parc (DREAL / organisations)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.parc import crud
from api.parc.deps import MembreContext, get_membre_context
from api.parc.schemas import CaseBilanMatrice, ProjetParc, SyntheseParc

router = APIRouter(prefix="/parc")


@router.get("/synthese", response_model=SyntheseParc)
def synthese_parc(
    membre: Annotated[MembreContext, Depends(get_membre_context)],
) -> SyntheseParc:
    try:
        return crud.lire_synthese_parc(
            role=membre.role,
            organisation_id=membre.organisation_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Synthèse parc impossible : {exc}",
        ) from exc


@router.get("/projets", response_model=list[ProjetParc])
def projets_parc(
    membre: Annotated[MembreContext, Depends(get_membre_context)],
    organisation_id: UUID | None = Query(default=None),
    departement: str | None = Query(default=None),
    gravite: int | None = Query(default=None, ge=0, le=2),
    signal: str | None = Query(default=None),
    statut: str | None = Query(default=None),
    q: str | None = Query(default=None),
    file_controle: bool = Query(
        default=False,
        description="Si true, ne retourne que les projets à gravité > 0.",
    ),
) -> list[ProjetParc]:
    try:
        return crud.lister_projets_parc(
            role=membre.role,
            organisation_id=membre.organisation_id,
            organisation_filtre=organisation_id,
            departement=departement,
            gravite=gravite,
            signal=signal,
            statut=statut,
            q=q,
            file_controle_only=file_controle,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Liste parc impossible : {exc}",
        ) from exc


@router.get("/bilans-matrice", response_model=list[CaseBilanMatrice])
def bilans_matrice(
    membre: Annotated[MembreContext, Depends(get_membre_context)],
    organisation_id: UUID | None = Query(default=None),
    annee_min: int | None = Query(default=None),
    annee_max: int | None = Query(default=None),
) -> list[CaseBilanMatrice]:
    try:
        return crud.lister_bilans_matrice(
            role=membre.role,
            organisation_id=membre.organisation_id,
            annee_min=annee_min,
            annee_max=annee_max,
            organisation_filtre=organisation_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Matrice bilans impossible : {exc}",
        ) from exc
