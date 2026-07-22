"""Schémas Pydantic pour l'API parc."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SignalParc(BaseModel):
    code: str
    niveau: str
    libelle: str
    valeur: float | None = None
    detail: dict[str, Any] | list[Any] | None = None


class SignalCompte(BaseModel):
    code: str
    nb_projets: int
    nb_critiques: int = 0


class SyntheseParc(BaseModel):
    nb_projets: int
    nb_organisations: int
    budget_total_sous_obligation: float
    realise_cumule: float
    realise_annee_courante: float
    nb_projets_conformes: int
    nb_projets_attention: int
    nb_projets_critiques: int
    bilans_attendus: int
    bilans_valides: int
    taux_remise_bilans: float | None
    signaux_par_code: list[SignalCompte]


class ProjetParc(BaseModel):
    projet_id: UUID
    nom: str
    reference_interne: str | None = None
    organisation_id: UUID
    organisation_nom: str
    commune: str | None = None
    departement: str | None = None
    type_procedure: str | None = None
    statut: str
    date_decision: date | None = None
    duree_annees: int | None = None
    date_fin: date | None = None
    gravite: int
    nb_signaux_critiques: int
    nb_signaux_attention: int
    signaux: list[SignalParc] = Field(default_factory=list)
    total_initial: float
    total_prevu: float
    total_engage: float
    total_realise: float
    delta_total: float
    prevu_annee_courante: float
    realise_annee_courante: float
    premiere_annee: int | None = None
    derniere_annee: int | None = None
    nb_occurrences: int
    nb_occurrences_realisees: int
    nb_occurrences_reportees: int
    nb_bilans_valides: int
    nb_bilans_manquants: int
    dernier_bilan_valide: int | None = None


class CaseBilanMatrice(BaseModel):
    projet_id: UUID
    projet_nom: str
    organisation_id: UUID
    organisation_nom: str
    annee: int
    etat: str
    rapport_id: UUID | None = None
    version: int | None = None
    valide_le: datetime | None = None
