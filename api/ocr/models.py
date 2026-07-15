"""
models.py — Contrat partagé du pipeline de bancarisation.

Deux objets, deux natures :
  - Echeance   : le TEMPLATE extrait du plan de gestion (une RÈGLE récurrente).
                 Immuable, tracé jusqu'à la page du PDF. Produit par l'étape 2 (LLM).
  - Occurrence : une INSTANCE datée, matérialisée en base, propriété du user (CRUD).
                 Produite UNE FOIS par le moteur, puis vit sa vie.

▲ CORRECTIF (null-tolérance). Un LLM qui raisonne bien envoie `null` pour un objet
  qui n'a pas de sens dans le contexte : un bilan annuel (MG1) ou une mise à jour
  quinquennale (MG2) n'a PAS de fenêtre d'intervention saisonnière. C'est
  sémantiquement juste — c'était le schéma qui était trop rigide.
  Les BeforeValidator ci-dessous convertissent None → valeur par défaut AVANT
  validation, ce qui a deux vertus :
    1. on accepte les deux représentations du LLM (null OU objet à champs nuls) ;
    2. le code aval (occurrences.py) ne voit JAMAIS de None et n'a pas à s'en
       protéger — pas d'AttributeError sur `e.fenetre_intervention.debut`.

Extension implémentée (additive, sans migration grâce au jsonb en base) :
TypeRecurrence.paliers pour les suivis à cadence dégressive (SE1).
Chaque palier enchaîne depuis la DERNIÈRE occurrence émise, pas le début du segment.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field, field_validator

# --- Vocabulaires contrôlés --------------------------------------------------

TypeOperation = Literal["EP", "TU", "TE", "SE", "MG"]

# Aligné sur les StatutChip / StatutBadge du frontend.
Statut = Literal[
    "planifie",
    "a_confirmer",
    "en_attente",
    "partiel",
    "realise",
    "retard",
    "supprime",
]

Origine = Literal["ia", "user"]

VOCAB_TYPE_METIER = [
    "replantation", "eclaircie", "gyrobroyage", "rotobroyage", "etrepage",
    "arrachage_eee", "abattage", "fauche", "rouleau_brise_fougere",
    "suivi_flore", "suivi_faune", "suivi_habitats", "pilotage", "bilan",
    "autre",
]


class TypeRecurrence(str, Enum):
    ponctuel = "ponctuel"                        # action unique
    periodique = "periodique"                    # tous les N ans
    campagnes = "campagnes"                      # K fois/an pendant M ans
    dependant_evenement = "dependant_evenement"  # N ans après un autre événement
    paliers = "paliers"                          # cadence dégressive par segments


class Palier(BaseModel):
    """
    Segment de cadence pour type ``paliers``.

    Chaque occurrence du palier est espacée de ``intervalle_ans`` par rapport à la
    DERNIÈRE occurrence déjà émise — pas par rapport au début du segment calendaire.

    Exemple SE1 (page 9 du plan) : ancrage 2019 (état zéro), puis palier annuel × 4
    → 2020…2023, puis palier triennal × 5 → 2026 (2023+3), 2029, 2032, 2035, 2038.
    """
    intervalle_ans: float = Field(gt=0)
    nombre_occurrences: int = Field(ge=1)


# --- Tolérance aux null du LLM ----------------------------------------------

def _liste(v: Any) -> Any:
    """null / absent → liste vide."""
    return [] if v is None else v


def _paliers(v: Any) -> Any:
    return [] if v is None else v


ListeStr = Annotated[list[str], BeforeValidator(_liste)]
ListePalier = Annotated[list[Palier], BeforeValidator(_paliers)]


# --- Échéance (template) -----------------------------------------------------

class Recurrence(BaseModel):
    type: TypeRecurrence
    intervalle_ans: Optional[float] = None      # si type == periodique
    occurrences_par_an: Optional[int] = None    # si type == campagnes
    duree_ans: Optional[int] = None             # si type == campagnes
    ancrage_annee: Optional[int] = None         # None = à compléter par le user
    regle_source: Optional[str] = None          # texte brut de la règle
    paliers: ListePalier = Field(default_factory=list)  # si type == paliers


class FenetreIntervention(BaseModel):
    """
    Fenêtre saisonnière autorisée. Tous les champs sont optionnels : une tâche
    administrative (bilan, reporting) n'a pas de fenêtre. Un objet vide signifie
    « pas de contrainte saisonnière » — le moteur ne la placera pas sur la grille
    mensuelle du calendrier.
    """
    debut: Optional[str] = None                 # "MM-DD"
    fin: Optional[str] = None                   # "MM-DD"
    traverse_nouvel_an: bool = False
    texte_source: Optional[str] = None


class Source(BaseModel):
    page: Optional[int] = None
    extrait: Optional[str] = None


def _fenetre(v: Any) -> Any:
    """null → fenêtre vide (le LLM a raison : certaines tâches n'en ont pas)."""
    return FenetreIntervention() if v is None else v


def _source(v: Any) -> Any:
    return Source() if v is None else v


class Echeance(BaseModel):
    id: str                                     # clé métier stable
    code_operation: str                         # normalisé "TU 1" → "TU1"
    type_operation: TypeOperation
    type_metier: str
    libelle: str
    objectif_long_terme: Optional[str] = None
    objectif_operationnel: Optional[str] = None
    unites_gestion: ListeStr = Field(default_factory=list)
    parcelles: ListeStr = Field(default_factory=list)
    communes: ListeStr = Field(default_factory=list)
    recurrence: Recurrence                      # essentiel : reste strict
    fenetre_intervention: Annotated[
        FenetreIntervention, BeforeValidator(_fenetre)
    ] = Field(default_factory=FenetreIntervention)
    conditions: ListeStr = Field(default_factory=list)
    indicateurs: ListeStr = Field(default_factory=list)
    intervenants: ListeStr = Field(default_factory=list)
    duree_gestion_ans: Optional[int] = None
    source: Annotated[Source, BeforeValidator(_source)] = Field(default_factory=Source)
    confiance: float = Field(ge=0.0, le=1.0)
    champs_a_confirmer: ListeStr = Field(default_factory=list)
    avertissements: ListeStr = Field(default_factory=list)

    @field_validator("code_operation")
    @classmethod
    def _normaliser_code(cls, v: str) -> str:
        """
        Les LLM alternent entre "TU1" et "TU 1" selon l'humeur et le document.
        On normalise sans espace : c'est la clé stockée en base, elle doit être
        stable quel que soit le modèle d'extraction utilisé.
        """
        return v.replace(" ", "").strip().upper()


class ExtractionResult(BaseModel):
    """Sortie de l'étape 2 (LLM)."""
    echeances: list[Echeance]


# --- Dossier (métadonnées projet) --------------------------------------------

class HorizonGestion(BaseModel):
    annee_debut: Optional[int] = None
    annee_fin: Optional[int] = None
    duree_ans: Optional[int] = None
    champs_a_confirmer: ListeStr = Field(default_factory=list)
    avertissements: ListeStr = Field(default_factory=list)


class ZoneCompensation(BaseModel):
    nom: str
    commune: Optional[str] = None
    superficie: Optional[str] = None
    especes_cibles: ListeStr = Field(default_factory=list)


class UniteGestionDossier(BaseModel):
    id: str
    objectif: Optional[str] = None
    communes: ListeStr = Field(default_factory=list)
    parcelles: ListeStr = Field(default_factory=list)


class DossierMetadata(BaseModel):
    """Identité du projet / dossier de compensation."""
    nom_operation: Optional[str] = None
    maitre_ouvrage: Optional[str] = None
    operateur: Optional[str] = None
    communes: ListeStr = Field(default_factory=list)
    arrete_numero: Optional[str] = None
    arrete_date: Optional[str] = None
    horizon: HorizonGestion = Field(default_factory=HorizonGestion)
    especes_protegees: ListeStr = Field(default_factory=list)
    especes_detruites: ListeStr = Field(default_factory=list)
    milieux_cibles: ListeStr = Field(default_factory=list)
    zones: list[ZoneCompensation] = Field(default_factory=list)
    unites_gestion: list[UniteGestionDossier] = Field(default_factory=list)
    budget_global_ht: Optional[float] = None
    budget_devise: Optional[str] = "EUR"
    dette_ecologique: Optional[str] = None
    type_obligation: Optional[str] = None
    intervenants: ListeStr = Field(default_factory=list)
    confiance: float = Field(default=0.5, ge=0.0, le=1.0)
    champs_a_confirmer: ListeStr = Field(default_factory=list)
    avertissements: ListeStr = Field(default_factory=list)


class DossierResult(BaseModel):
    dossier: DossierMetadata


# --- Actions (fiches complètes) ----------------------------------------------

class ActionFiche(BaseModel):
    """
    Fiche-action du plan de gestion (TU1, TE1, SE1…).
    `contenu_integral` = texte OCR repris intégralement, sans résumé.
    """
    id: str
    code: str
    categorie: TypeOperation
    titre: str
    objectif_long_terme: Optional[str] = None
    objectif_operationnel: Optional[str] = None
    unites_gestion: ListeStr = Field(default_factory=list)
    parcelles: ListeStr = Field(default_factory=list)
    communes: ListeStr = Field(default_factory=list)
    cadrage_surfacique: Optional[str] = None
    description: Optional[str] = None
    engagements: ListeStr = Field(default_factory=list)
    indicateurs: ListeStr = Field(default_factory=list)
    intervenants: ListeStr = Field(default_factory=list)
    periodicite_texte: Optional[str] = None
    frise_markdown: Optional[str] = None
    contenu_integral: str
    pages: list[int] = Field(default_factory=list)
    confiance: float = Field(default=1.0, ge=0.0, le=1.0)
    champs_a_confirmer: ListeStr = Field(default_factory=list)
    avertissements: ListeStr = Field(default_factory=list)

    @field_validator("code")
    @classmethod
    def _normaliser_code_action(cls, v: str) -> str:
        return v.replace(" ", "").strip().upper()

    @field_validator("id")
    @classmethod
    def _normaliser_id(cls, v: str) -> str:
        return v.replace(" ", "").strip().upper()


class ActionsResult(BaseModel):
    actions: list[ActionFiche]


class EcheanceLiee(Echeance):
    """Échéance enrichie après liaison déterministe avec une action."""
    action_id: Optional[str] = None


class EcheancesLieesResult(BaseModel):
    echeances: list[EcheanceLiee]
    liaisons: dict[str, str] = Field(default_factory=dict)
    orphelines: ListeStr = Field(default_factory=list)


# --- Occurrence (instance matérialisée) --------------------------------------

class Occurrence(BaseModel):
    """
    Sortie du moteur. Mappe 1:1 sur bancarisation.occurrence et sur
    l'OccurrenceView du frontend.
    """
    echeance_cle: Optional[str] = None          # None si créée à la main par le user
    annee: int
    code: str                                   # "TU 3" (formaté pour l'affichage)
    titre: str
    categorie: TypeOperation
    statut: Statut = "planifie"
    ug_ids: list[str] = Field(default_factory=list)
    mois_debut: Optional[int] = None
    mois_fin: Optional[int] = None
    traverse_nouvel_an: bool = False
    origine: Origine = "ia"
    confiance: Optional[float] = None
    champs_a_confirmer: list[str] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)