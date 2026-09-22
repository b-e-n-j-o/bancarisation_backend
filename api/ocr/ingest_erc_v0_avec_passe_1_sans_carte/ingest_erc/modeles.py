"""Contrats de données de la passe 1 (inventaire -> référentiel proposé).

Tout ce qui sort d'un extracteur est un `Fait` sourcé. La réconciliation
ne manipule que des faits ; l'écran BE ne manipule que des `Cellule`.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

Role = Literal["arrete", "plan_gestion", "tableur", "sig", "execution", "autre"]
Confiance = Literal["haute", "moyenne", "basse"]


class Source(BaseModel):
    doc: str                      # nom du fichier
    loc: str                      # "p.40", "Synthèse actions!H3", "Compensation_Fadet.shp#3"
    extrait: Optional[str] = None  # court extrait pour l'écran de validation


class Fait(BaseModel):
    """Affirmation atomique extraite d'un document."""
    type: str                     # "action_ug", "ug_libelle", "ancre_temporelle", ...
    cle: str                      # clé de regroupement : "TU1", "UG5", "projet"
    valeur: Any
    source: Source
    methode: Literal["deterministe", "llm", "user"] = "deterministe"
    confiance_extraction: float = 1.0


# ---------------------------------------------------------------- inventaire
class PageTexte(BaseModel):
    num: int
    texte: str                            # texte normalisé utilisé par les regex
    nb_mots: int                          # mots de la couche texte NATIVE (0 = page scannée)
    markdown: Optional[str] = None        # sortie brute Mistral OCR (tableaux)
    texte_natif: Optional[str] = None     # pdftotext, si disponible
    fiabilite_ocr: Optional[float] = None # part des nombres OCR retrouvés dans la couche native


class Document(BaseModel):
    nom: str
    chemin: str
    sha256: str
    extension: str
    role: Role
    role_indice: str = ""                     # pourquoi ce rôle
    pages: list[PageTexte] = Field(default_factory=list)
    pages_image: list[int] = Field(default_factory=list)   # pages sans couche texte
    sous_documents: list[dict] = Field(default_factory=list)  # annexes détectées
    lignes_repetees: list[str] = Field(default_factory=list)  # en-têtes / pieds retirés
    avertissements: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- SIG
class ColonneProfil(BaseModel):
    nom: str
    nb_valeurs: int
    exemples: list[str]
    interpretation: Literal["ug_plan", "ref_cadastrale", "surface", "autre_nomenclature",
                            "annees", "inconnu"] = "inconnu"


class ZoneCandidate(BaseModel):
    id: str                        # "Compensation_Fadet::Action=UG 2"
    couche: str
    filtre: Optional[dict] = None  # {"Action": "UG 2"} ou None = couche entière
    ug_attribut: Optional[str] = None   # "UG2" si lu dans un attribut
    nb_entites: int
    surface_ha: float
    refs_cadastrales: list[str] = Field(default_factory=list)
    type_erc_nom: Optional[str] = None  # déduit du nom de couche (Compensation/Evitement)


class CoucheProfil(BaseModel):
    nom: str
    fichier: str
    geom_type: str
    nb_entites: int
    crs_source: str
    epsg: Optional[int]
    reprojetee: bool
    surface_ha: float
    colonnes: list[ColonneProfil]
    avertissements: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- sortie écran BE
class Cellule(BaseModel):
    valeur: Any
    confiance: Confiance
    sources: list[Source] = Field(default_factory=list)
    note: Optional[str] = None


class LigneUG(BaseModel):
    ug_code: str
    libelle: Cellule
    type_erc: Cellule
    zone_sig: Cellule          # id de ZoneCandidate
    surface_ha: Cellule


class LigneAction(BaseModel):
    code: str
    intitule: Cellule
    nature: Cellule
    ugs: Cellule
    cible: Cellule


class Question(BaseModel):
    id: str
    portee: str                 # "projet", "UG5", "TE1"...
    texte: str
    options: list[str] = Field(default_factory=list)
    proposition: Optional[str] = None
    sources: list[Source] = Field(default_factory=list)


class ReferentielPropose(BaseModel):
    projet: dict[str, Cellule]
    ugs: list[LigneUG]
    actions: list[LigneAction]
    obligations_surfaciques: list[dict] = Field(default_factory=list)
    questions: list[Question]
    controles: list[dict] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
