"""Tous les modèles de données du pipeline, regroupés par étape.

Deux familles :
- les modèles « *LLM » : ce que l'on demande au modèle de produire (schémas
  volontairement compacts pour maximiser le respect du format) ;
- les modèles internes : ce que les étapes s'échangent et écrivent sur disque.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Étape 1 — ingestion
# =============================================================================
class Page(BaseModel):
    index: int                        # 0-based dans le PDF
    number: int                       # 1-based dans le PDF
    printed_number: Optional[str] = None   # numéro imprimé en pied de page (ex. "185")
    source: Literal["native", "ocr"] = "native"
    char_count: int = 0
    text: str = ""                    # texte natif (layout) ou markdown OCR
    ocr_tables: list[str] = []        # tableaux renvoyés séparément par l'OCR
    ocr_confidence: Optional[float] = None


class DocumentText(BaseModel):
    path: str
    sha256: str
    n_pages: int
    pages: list[Page]

    def page(self, number: int) -> Page:
        return self.pages[number - 1]


# =============================================================================
# Étape 2 — repérage
# =============================================================================
PageRole = Literal[
    "cadre",              # durée, période couverte, définition de N, opérateur
    "unites_gestion",     # définition des UG / secteurs
    "catalogue_actions",  # liste / tableau récapitulatif des actions
    "fiche_action",       # fiche détaillée d'une action
    "programmation",      # calendrier / grille pluriannuelle
    "suivi_evaluation",   # indicateurs, protocoles de suivi, bilans
    "couts",
    "hors_plan",          # page du document qui ne relève pas du plan de gestion
    "autre",
]


class PageRoleLLM(BaseModel):
    number: int
    roles: list[PageRole]
    fiche_code: Optional[str] = Field(None, description="Code de l'action si la page est (la suite d') une fiche")
    in_plan_de_gestion: bool = True


class PageRolesLLM(BaseModel):
    pages: list[PageRoleLLM]


class Evidence(BaseModel):
    page: int
    text: str


class PageInfo(BaseModel):
    number: int
    roles: list[str] = []
    fiche_code: Optional[str] = None
    in_plan_de_gestion: bool = True
    codes: list[str] = []             # codes d'action vus sur la page (regex)
    grid_hint: bool = False           # en-têtes N / N+k / années / mois détectés


class DocumentMap(BaseModel):
    pages: list[PageInfo]
    pg_pages: list[int]               # pages retenues comme « plan de gestion »
    code_candidates: dict[str, int]   # code -> nb d'occurrences
    ug_candidates: dict[str, int]
    horizon_evidence: list[Evidence] = []
    n_year_evidence: list[Evidence] = []


# =============================================================================
# Étape 3 — grilles
# =============================================================================
Axis = Literal["rel_year", "abs_year", "month"]


class GridColumn(BaseModel):
    label: str
    axis: Axis
    rel_start: Optional[int] = None
    rel_end: Optional[int] = None
    year_start: Optional[int] = None
    year_end: Optional[int] = None
    month: Optional[int] = None
    x0: float = 0
    x1: float = 0


class GridCell(BaseModel):
    cols: list[int]                   # index des colonnes couvertes
    kind: Literal["mark", "band", "fill"]
    text: str = ""
    fill: Optional[str] = None


class GridRow(BaseModel):
    code: Optional[str] = None
    action_label: str = ""
    sub_label: str = ""
    cells: list[GridCell] = []
    agreement: Optional[float] = None  # accord géométrie / vision (0-1)


class Grid(BaseModel):
    id: str
    page: int
    axis: Axis
    columns: list[GridColumn]
    rows: list[GridRow]
    n_year: Optional[int] = None      # année de N déduite de l'en-tête (N <-> 2025)
    source: Literal["geometry", "vision", "merged"] = "geometry"
    notes: list[str] = []


class VisionCellLLM(BaseModel):
    columns: list[str] = Field(description="Libellés exacts des colonnes couvertes par la cellule")
    kind: Literal["mark", "band", "fill"]
    text: str = ""


class VisionRowLLM(BaseModel):
    code: Optional[str] = None
    action_label: str = ""
    sub_label: str = ""
    cells: list[VisionCellLLM] = []


class VisionGridLLM(BaseModel):
    axis: Axis
    columns: list[str]
    rows: list[VisionRowLLM]


class VisionPageLLM(BaseModel):
    grids: list[VisionGridLLM] = []


# =============================================================================
# Étape 4 — extraction (cadre + règles)
# =============================================================================
class Citation(BaseModel):
    page: int = Field(description="Numéro de page PDF, tel qu'indiqué dans le marqueur [PAGE n]")
    quote: str = Field(description="Extrait copié mot pour mot, 200 caractères max")


class UGLLM(BaseModel):
    code: str
    label: str = ""
    surface_m2: Optional[float] = None
    citations: list[Citation] = []


class ActionRefLLM(BaseModel):
    code: str
    title: str
    category: str = Field("", description="Famille telle que nommée dans le document (restauration, suivi, animation…)")
    ug_codes: list[str] = []
    site_wide: bool = False
    citations: list[Citation] = []


class FrameLLM(BaseModel):
    duration_years: Optional[int] = Field(None, description="Durée de gestion couverte par le plan")
    n_definition: str = Field("", description="Ce que représente l'année N selon le document")
    n_year: Optional[int] = Field(None, description="Année calendaire de N si le document la donne")
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    operator: str = ""
    ugs: list[UGLLM] = []
    actions: list[ActionRefLLM] = []
    citations: list[Citation] = []
    notes: list[str] = []


class PassLLM(BaseModel):
    label: str = ""
    months: list[int] = Field(default_factory=list, description="Mois 1-12 de la fenêtre d'intervention")
    optional: bool = Field(False, description="Passage facultatif (ex. 2e fauche « selon besoin »)")


class SegmentLLM(BaseModel):
    years: Optional[list[int]] = Field(None, description="Années relatives explicites (0 = N)")
    start: Optional[int] = Field(None, description="Première année relative de la série périodique")
    end: Optional[int] = Field(None, description="Dernière année relative (null = fin du plan)")
    step_min: int = Field(1, description="Intervalle en années (min)")
    step_max: int = Field(1, description="Intervalle en années (max) ; = step_min si fixe")
    count_over_period: Optional[int] = Field(None, description="Nb total d'interventions à répartir sur [start, end]")


RuleKind = Literal["ponctuelle", "recurrente", "continue", "conditionnelle", "non_precisee"]


class RuleLLM(BaseModel):
    action_code: str
    sub_action: str
    ug_codes: list[str] = []
    kind: RuleKind
    anchor: str = Field("N", description="Événement de référence des années relatives si ce n'est pas N")
    segments: list[SegmentLLM] = []
    passes: list[PassLLM] = []
    frequency_text: str = Field("", description="Formulation exacte de la fréquence dans le document")
    window_text: str = Field("", description="Formulation exacte de la période d'intervention")
    trigger: str = Field("", description="Condition de déclenchement si conditionnelle")
    actor: str = ""
    citations: list[Citation] = []
    confidence: float = Field(0.5, ge=0, le=1)
    ambiguities: list[str] = []


class RulesLLM(BaseModel):
    rules: list[RuleLLM]


class CriticLLM(BaseModel):
    rules: list[RuleLLM]
    changes: list[str] = Field(default_factory=list, description="Corrections apportées, une par ligne, avec justification")
    missing_actions: list[str] = []


# =============================================================================
# Étapes 5-6 — règles vérifiées et occurrences
# =============================================================================
Decision = Literal["integre", "a_valider", "a_revoir", "declencheur", "a_preciser"]


class ConfidenceDetail(BaseModel):
    evidence: float = 0.0             # part des citations retrouvées dans le texte
    agreement: float = 0.0            # accord entre échantillons LLM
    grid: Optional[float] = None      # accord avec la grille (None = pas de grille)
    specificity: float = 1.0          # pénalités fourchettes / flou
    llm: float = 0.5                  # auto-évaluation du modèle
    score: float = 0.0


class Rule(RuleLLM):
    rule_id: str = ""
    support: float = 1.0              # part des échantillons qui contiennent la règle
    issues: list[str] = []
    critic_changes: list[str] = []
    months_from_grid: bool = False
    confidence_detail: ConfidenceDetail = Field(default_factory=ConfidenceDetail)
    decision: Decision = "a_revoir"


class Frame(FrameLLM):
    n_year_from_grid: Optional[int] = None
    issues: list[str] = []


class Occurrence(BaseModel):
    occ_id: str
    rule_id: str
    action_code: str
    sub_action: str
    ug_codes: list[str]
    rel_year: Optional[int]
    year: Optional[int]
    span_end_rel: Optional[int] = None    # activités continues : barre de N à N+x
    months: list[int] = []
    pass_label: str = ""
    optional: bool = False
    hypothesis: str = ""                  # non vide si l'année dépend d'une hypothèse
    decision: Decision
    confidence: float
    source_pages: list[int] = []


class CalendarResult(BaseModel):
    document: str
    settings: dict
    frame: Frame
    rules: list[Rule]
    occurrences: list[Occurrence]
    issues: list[str] = []
    stats: dict = {}
