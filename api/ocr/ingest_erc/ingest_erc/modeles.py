"""Contrats de données de la passe 1 (inventaire -> référentiel proposé -> verrouillé).

Tout ce qui sort d'un extracteur est un `Fait` sourcé. La réconciliation
ne manipule que des faits ; l'écran BE ne manipule que des `Cellule`.
Les réponses et corrections du BE redeviennent des faits (methode="user"),
puis la réconciliation est rejouée : c'est la réinjection.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

Role = Literal["arrete", "plan_gestion", "tableur", "sig", "execution", "autre"]
Confiance = Literal["haute", "moyenne", "basse"]


class Source(BaseModel):
    doc: str                      # nom du fichier ("BE" pour une saisie de validation)
    loc: str                      # "p.40", "Synthèse actions!H3", "Compensation_Fadet.shp#3"
    extrait: Optional[str] = None  # court extrait pour l'écran de validation


class Fait(BaseModel):
    """Affirmation atomique extraite d'un document ou saisie par le BE.

    Types réservés à la réinjection (methode="user") :
      reponse     cle = id de question ("regle:portee"), valeur = option choisie ou texte libre
      correction  cle = "projet.<champ>" | "ug.<code>.<champ>" | "action.<code>.<champ>"
      renommage   cle = "ug.<ancien>" | "action.<ancien>", valeur = nouveau code
      ug_ajout / action_ajout / ug_supprime / action_supprime   cle = code
    """
    type: str
    cle: str
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
    role_indice: str = ""
    pages: list[PageTexte] = Field(default_factory=list)
    pages_image: list[int] = Field(default_factory=list)
    sous_documents: list[dict] = Field(default_factory=list)
    lignes_repetees: list[str] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- SIG
class ColonneProfil(BaseModel):
    nom: str
    nb_valeurs: int
    exemples: list[str]
    interpretation: Literal["ug_plan", "ref_cadastrale", "surface", "autre_nomenclature",
                            "annees", "inconnu"] = "inconnu"


class ZoneCandidate(BaseModel):
    id: str
    couche: str
    filtre: Optional[dict] = None
    ug_attribut: Optional[str] = None
    nb_entites: int
    surface_ha: float
    refs_cadastrales: list[str] = Field(default_factory=list)
    type_erc_nom: Optional[str] = None


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
    zone_sig: Cellule
    surface_ha: Cellule


class LigneAction(BaseModel):
    code: str
    intitule: Cellule
    nature: Cellule
    ugs: Cellule
    cible: Cellule


class Question(BaseModel):
    id: str                     # "regle:portee" — STABLE d'une relance à l'autre
    regle: str = ""             # règle de réconciliation qui l'a produite
    portee: str                 # "projet", "UG5", "TE1", id de zone, référence de parcelle…
    texte: str
    options: list[str] = Field(default_factory=list)
    proposition: Optional[str] = None
    sources: list[Source] = Field(default_factory=list)
    bloquante: bool = True      # empêche le verrouillage tant qu'elle est ouverte


class ReferentielPropose(BaseModel):
    projet: dict[str, Cellule]
    ugs: list[LigneUG]
    actions: list[LigneAction]
    obligations_surfaciques: list[dict] = Field(default_factory=list)
    questions: list[Question]
    anomalies: list[dict] = Field(default_factory=list)
    controles: list[dict] = Field(default_factory=list)
    # Décisions tirées des réponses BE, consommées par la passe 3 :
    # alias_codes, alias_parcelles, parcelles_a_ajouter, requalifications_blocs,
    # couches_contexte, couches_ignorees, decisions_annexes, roles_tableaux
    decisions: dict = Field(default_factory=dict)
    stats: dict = Field(default_factory=dict)


# ---------------------------------------------------------------- référentiel verrouillé
class UGVerrouillee(BaseModel):
    ug_code: str
    libelle: Any = None
    type_erc: Any = None
    zone_sig: Any = None        # id de ZoneCandidate, ou None = sans géométrie (assumé)
    surface_ha: Any = None


class ActionVerrouillee(BaseModel):
    code: str
    intitule: Any = None
    nature: Any = None
    ugs: list[str] = Field(default_factory=list)
    cible: Any = None


class ReferentielVerrouille(BaseModel):
    """Entrée UNIQUE de la passe 3. Valeurs nues : la confiance a été tranchée par le BE."""
    version: int = 1
    projet: dict[str, Any]
    ugs: list[UGVerrouillee]
    actions: list[ActionVerrouillee]
    obligations_surfaciques: list[dict] = Field(default_factory=list)
    decisions: dict = Field(default_factory=dict)
    # "projet.annee_N" -> {"origine": "be" | "accepte", "confiance": "haute"|..., "sources": [...]}
    provenance: dict[str, dict] = Field(default_factory=dict)
    questions_forcees: list[str] = Field(default_factory=list)   # verrouillé malgré ces questions
    empreinte: str               # sha256 du contenu : chaque résultat de passe 3 la porte
    verrouille_le: str