"""Schémas Pydantic — couverture prescription ↔ échéance."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class EcheanceMiniOut(BaseModel):
    id: UUID
    code_operation: str | None = None
    type_operation: str | None = None
    type_metier: str | None = None
    libelle: str | None = None
    objectif_operationnel: str | None = None
    lib_thema: str | None = None
    recurrence: dict[str, Any] | None = None
    action_cle: str | None = None


class PrescriptionMiniOut(BaseModel):
    id: UUID
    article: str | None = None
    intitule: str
    categorie: str | None = None
    nature: str | None = None
    cible_valeur: float | None = None
    cible_unite: str | None = None
    echeance: date | None = None
    page_source: int | None = None
    texte_source: str | None = None


class LienCouvertureOut(BaseModel):
    prescription_id: UUID
    echeance_id: UUID
    mode: Literal["ia", "user"]
    confiance: float | None = None
    note: str | None = None
    cree_le: datetime | None = None
    # enrichissement lecture
    prescription: PrescriptionMiniOut | None = None
    echeance: EcheanceMiniOut | None = None


class CouvertureSyntheseOut(BaseModel):
    prescription_id: UUID
    nb_echeances: int = 0
    nb_occurrences: int = 0
    nb_realisees: int = 0
    nb_en_retard: int = 0
    prochaine_echeance: date | None = None
    couverture: Literal["non_couverte", "couverte"] = "non_couverte"


class AppariementEtatOut(BaseModel):
    projet_id: UUID
    arrete_id: UUID
    prescriptions: list[PrescriptionMiniOut]
    echeances: list[EcheanceMiniOut]
    liens: list[LienCouvertureOut]
    syntheses: list[CouvertureSyntheseOut]
    nb_propositions_ia: int = 0
    nb_valides: int = 0
    nb_non_couvertes: int = 0


class ApparierResultOut(BaseModel):
    liens_inseres: int
    usage: dict[str, Any] = Field(default_factory=dict)
    etat: AppariementEtatOut


class LienBody(BaseModel):
    prescription_id: UUID
    echeance_id: UUID
