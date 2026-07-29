"""Schémas Pydantic — couche contrôle DREAL."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


StatutControle = Literal[
    "en_suivi",
    "a_instruire",
    "complement_attente",
    "non_conforme",
    "mise_en_demeure",
    "clos",
]

TypeArrete = Literal[
    "declaration_loi_eau",
    "autorisation_env",
    "derogation_ep",
    "arrete_modificatif",
    "autre",
]

StatutBilanSuivi = Literal[
    "attendu",
    "depose",
    "en_relecture",
    "complement_demande",
    "valide",
    "rejete",
]


class ArretePrescriptionOut(BaseModel):
    id: UUID | None = None
    article: str | None = None
    intitule: str
    categorie: str | None = None
    nature: str | None = None
    cible_valeur: float | None = None
    cible_unite: str | None = None
    echeance: date | None = None
    recurrence: dict[str, Any] | None = None
    page_source: int | None = None
    texte_source: str | None = None
    confiance: float | None = None


class ArreteOut(BaseModel):
    id: UUID
    projet_id: UUID
    type: str
    reference: str | None = None
    date_notification: date | None = None
    date_signature: date | None = None
    document_id: UUID | None = None
    autorite: str | None = None
    beneficiaire: str | None = None
    numero_dossier: str | None = None
    confiance: float | None = None
    extraction_modele: str | None = None
    rubriques: list[str] = Field(default_factory=list)
    prescriptions: list[ArretePrescriptionOut] = Field(default_factory=list)


class LigneConformiteOut(BaseModel):
    mesure_id: UUID  # action_fiche_id
    code: str
    libelle: str
    prescrit: dict[str, Any] = Field(default_factory=dict)
    constate: dict[str, Any] = Field(default_factory=dict)
    conformite: str
    ecart_explicite: str | None = None
    statut_execution: str | None = None
    nb_constats: int = 0


class BilanSuiviOut(BaseModel):
    id: UUID
    projet_id: UUID
    annee: int
    statut: str
    depose_le: datetime | None = None
    statue_le: datetime | None = None
    document_id: UUID | None = None
    rapport_suivi_id: UUID | None = None
    # Depuis rapport_suivi.controles (realise_sans_preuve / realise_sans_commentaire).
    alerte_justification: bool = False
    detail_justification: str | None = None
    nb_sans_preuve: int = 0
    nb_sans_commentaire: int = 0


class ActeDrealOut(BaseModel):
    id: UUID
    projet_id: UUID
    type: str
    statut: str
    objet: str | None = None
    contenu: dict[str, Any] = Field(default_factory=dict)
    delai_reponse: date | None = None
    emis_le: datetime | None = None
    document_id: UUID | None = None
    demande_id: UUID | None = None
    cree_le: datetime


class ConstatControleOut(BaseModel):
    id: UUID
    mesure_id: UUID | None = None  # action_fiche_id
    occurrence_id: UUID | None = None
    projet_id: UUID
    date: date
    auteur: str | None = None
    mode: str
    conformite: str
    commentaire: str | None = None
    pieces: list[str] = Field(default_factory=list)


class DossierControleOut(BaseModel):
    projet_id: UUID
    statut_controle: StatutControle
    statut_controle_override: StatutControle | None = None
    arretes: list[ArreteOut]
    lignes_conformite: list[LigneConformiteOut]
    bilans: list[BilanSuiviOut]
    actes: list[ActeDrealOut]
    constats: list[ConstatControleOut]


class ItemBannetteOut(BaseModel):
    id: str
    projet_id: UUID
    projet_nom: str
    organisation_nom: str
    motif: str
    libelle: str
    echeance: date | None = None
    priorite: int
    statut_controle: StatutControle
    gravite: int = 0
    acte_id: UUID | None = None
    bilan_id: UUID | None = None
    # True si le bilan lié a des occurrences sans preuve / sans précision BE.
    alerte_justification: bool = False
    detail_justification: str | None = None
    nb_sans_preuve: int = 0
    nb_sans_commentaire: int = 0


class PatchBilanBody(BaseModel):
    statut: StatutBilanSuivi


class GenererActeBody(BaseModel):
    type: Literal["demande_complement", "mise_en_demeure"] = "demande_complement"
    delai_jours: int = 30


class StatutProjetOut(BaseModel):
    projet_id: UUID
    statut_controle: StatutControle
    force_manuel: bool = False


class ActionFicheOption(BaseModel):
    id: UUID
    code: str
    titre: str
