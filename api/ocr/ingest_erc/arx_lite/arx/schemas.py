"""Format de sortie d'un arrêté analysé — et modèles intermédiaires du pipeline.

Le JSON final (`ArreteAnalyse`) a quatre niveaux de lecture, du plus général au plus fin :

1. `fiche`       — inventaire / métadonnées de l'acte (référence, autorité, dates, bénéficiaire,
                   bases juridiques, historique de l'instruction, actes liés, recours) ;
2. `synthese`    — résumé exhaustif par rubriques (justification, espèces, impacts autorisés,
                   périmètre et durées, sites, mesures, gouvernance, points clés) : survoler
                   l'arrêté sans le relire ;
3. `articles`    — chaque article / sous-article : texte, résumé, catégories, mesures,
                   obligations, références ; directement affichable en UI ;
4. `obligations` — toutes les obligations à plat, avec échéance structurée, preuve attendue,
                   statut (à évaluer plus tard contre les documents du dossier) ;
   + `echeancier` (dates calculées à partir d'ancres connues), `figures`, `controles`.

Toutes les informations importantes portent des `citations` (page + extrait exact), vérifiées.
Les dates sont au format ISO (AAAA-MM-JJ) ; les jours-mois récurrents au format MM-JJ.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Citation(BaseModel):
    page: int = Field(description="Numéro de page PDF, tel qu'indiqué par le marqueur [PAGE n]")
    quote: str = Field(description="Extrait copié mot pour mot, 250 caractères max")
    verified: Optional[bool] = None   # rempli par la vérification


# =============================================================================
# 1. Fiche (inventaire / métadonnées)
# =============================================================================
TypeActe = Literal[
    "derogation_especes_protegees", "autorisation_environnementale", "arrete_complementaire",
    "arrete_modificatif", "arrete_transfert", "prorogation", "mise_en_demeure", "sanction",
    "declaration_loi_eau", "autre",
]
RoleParty = Literal["beneficiaire", "petitionnaire", "autorite", "signataire", "service_instructeur",
                    "operateur_compensation", "ecologue", "destinataire_copie", "autre"]


class Party(BaseModel):
    role: RoleParty
    nom: str
    qualite: str = ""                  # « Directeur régional… », « SNC », « Présidente »
    adresse: str = ""
    identifiant: str = ""              # SIREN, RCS…
    contact: str = ""                  # mail / téléphone cités dans l'acte


class Visa(BaseModel):
    type: Literal["texte_legal", "arrete_ministeriel", "arrete_prefectoral", "delegation", "demande",
                  "complement", "avis", "reponse", "consultation_public", "autre"]
    libelle: str                       # libellé court (« avis du CSRPN »)
    date: Optional[str] = None
    date_fin: Optional[str] = None     # consultations sur une période
    auteur: str = ""
    reference: str = ""


class ActeLie(BaseModel):
    reference: str
    date: Optional[str] = None
    relation: Literal["modifie", "complete", "abroge", "proroge", "transfere", "vise", "autre"]
    libelle: str = ""


class Projet(BaseModel):
    nom: str = ""
    nature: str = ""                   # « opération immobilière de 234 logements »
    communes: list[str] = []
    departement: str = ""
    region: str = ""


class Recours(BaseModel):
    delai: str = ""
    juridiction: str = ""
    modalites: list[str] = []


class FicheLLM(BaseModel):
    reference: str = ""
    titre: str = ""
    type_acte: TypeActe = "autre"
    procedure: str = Field("", description="Procédure / régime (ex. dérogation art. L.411-2 C. env.)")
    autorite: str = ""
    date_signature: Optional[str] = None
    lieu_signature: str = ""
    projet: Projet = Field(default_factory=Projet)
    parties: list[Party] = []
    bases_juridiques: list[str] = Field(default_factory=list, description="Articles et textes visés, un par entrée")
    visas: list[Visa] = []
    actes_lies: list[ActeLie] = []
    recours: Recours = Field(default_factory=Recours)
    publication: str = ""
    copies: list[str] = []
    citations: list[Citation] = []


# =============================================================================
# 2. Synthèse (résumé exhaustif par rubriques)
# =============================================================================
GroupeEspece = Literal["flore", "oiseaux", "chiropteres", "mammiferes", "amphibiens", "reptiles",
                       "insectes", "poissons", "mollusques", "crustaces", "habitat", "autre"]
NatureDerogation = Literal["destruction_individus", "capture_enlevement", "perturbation_intentionnelle",
                           "destruction_habitat", "transport_detention", "coupe_arrachage", "autre"]


class Quantite(BaseModel):
    valeur: Optional[float] = None
    valeur_max: Optional[float] = None   # fourchettes « 10 à 15 »
    unite: str = ""                      # m², ha, m, unité, arbre, mare, nichoir…
    objet: str = ""                      # ce qui est compté


class Espece(BaseModel):
    nom_scientifique: str = ""
    nom_vernaculaire: str = ""
    groupe: GroupeEspece = "autre"
    natures: list[NatureDerogation] = []


class Impact(BaseModel):
    description: str
    quantites: list[Quantite] = []
    especes: list[str] = []
    citations: list[Citation] = []


class Duree(BaseModel):
    objet: str                           # « phase chantier », « compensation », « gestion du secteur évité »
    valeur: Optional[float] = None
    unite: str = ""                      # an, mois…
    minimale: bool = False
    date_fin: Optional[str] = None
    debut: str = ""                      # événement ou date de départ
    citations: list[Citation] = []


class Site(BaseModel):
    nom: str
    type: Literal["projet", "evite", "compensation_in_situ", "compensation_ex_situ", "autre"]
    communes: list[str] = []
    parcelles: list[str] = []
    surface_m2: Optional[float] = None
    operateur: str = ""
    description: str = ""
    figures: list[str] = []
    citations: list[Citation] = []


class Justification(BaseModel):
    resume: str = ""
    motifs: list[str] = []               # RIIPM, absence d'alternative, état de conservation…
    chiffres_cles: list[str] = []


class SyntheseLLM(BaseModel):
    resume_general: str = Field("", description="3 à 5 phrases : quoi, où, pour qui, quelles grandes mesures")
    justification: Justification = Field(default_factory=Justification)
    especes: list[Espece] = []
    impacts: list[Impact] = []
    perimetre: str = ""
    durees: list[Duree] = []
    sites: list[Site] = []
    gouvernance: str = ""
    points_cles: list[str] = Field(default_factory=list, description="5 à 12 points à retenir, concrets et chiffrés")
    citations: list[Citation] = []


class Synthese(SyntheseLLM):
    nb_especes: int = 0
    especes_par_groupe: dict[str, int] = {}
    especes_par_nature: dict[str, int] = {}
    mesures_par_type: dict[str, int] = {}


# =============================================================================
# 3. Articles, mesures, obligations
# =============================================================================
CategorieArticle = Literal[
    "objet", "nature_derogation", "perimetre", "duree", "prescriptions_generales", "suivi_chantier",
    "calendrier_travaux", "evitement", "reduction", "compensation", "accompagnement", "suivi_ecologique",
    "gouvernance", "transmission_documents", "foncier", "donnees", "incidents", "sanctions_controles",
    "recours", "execution", "autre",
]
TypeMesure = Literal["E", "R", "C", "A", "S"]
AnchorEvent = Literal[
    "notification", "signature", "publication", "demarrage_travaux", "fin_travaux",
    "demarrage_travaux_compensatoires", "fin_travaux_compensatoires", "livraison", "mise_en_service",
    "evenement_recurrent", "evenement_ponctuel", "autre",
]
PreuveType = Literal[
    "courrier", "planning", "plan_localisation", "cr_suivi_chantier", "journal_de_bord", "cr_abattage",
    "cr_sauvetage", "cr_travaux_compensatoires", "cr_suivi_ecologique", "bilan", "cr_comite",
    "plan_de_gestion", "acte_foncier", "contrat_operateur", "protocole", "note_technique",
    "fiche_geomce", "couche_sig_geomce", "recepisse_depobio", "bordereau_vegetaux", "cahier_entretien",
    "photo", "dossier_consultation_entreprises", "declaration_incident", "aucune", "autre",
]


class Mesure(BaseModel):
    code: str = Field(description="Code de la mesure dans l'acte (ex. « 11.2 », « MR3 ») ou numéro d'article")
    type: TypeMesure
    intitule: str
    localisation: Literal["site_projet", "in_situ", "ex_situ", "secteur_evite", "tous", "non_precise"] = "non_precise"
    site: str = ""
    especes_cibles: list[str] = []
    quantites: list[Quantite] = []
    prescriptions: list[str] = []        # prescriptions techniques, une par entrée
    references_dossier: list[str] = Field(default_factory=list, description="Codes du dossier cités (E02, R05…)")
    figures: list[str] = []
    citations: list[Citation] = []


class Echeance(BaseModel):
    kind: Literal[
        "date_fixe",            # « avant le 31/12/2028 »
        "delai_apres",          # « dans les 6 mois à compter de la notification »
        "delai_avant",          # « au plus tard 15 jours avant le démarrage »
        "date_annuelle",        # « au plus tard le 31 mars de l'année N+1 » / « le 31/12 de l'année de suivi »
        "annees_relatives",     # « N+4, N+6, N+11… » (avec day_month éventuel)
        "periode_autorisee",    # « entre le 1er septembre et mi-février »
        "periode_interdite",    # « aucune fauche de mai à juillet »
        "permanente",           # « pendant toute la durée du chantier »
        "sans_delai",           # « sans délai », « dès qu'il en a connaissance »
        "non_precisee",
    ]
    date: Optional[str] = None
    anchor: Optional[AnchorEvent] = None
    anchor_label: str = Field("", description="Événement d'ancrage tel qu'écrit (« passage de l'écologue »)")
    offset_value: Optional[int] = None
    offset_unit: Optional[Literal["jour", "semaine", "mois", "an"]] = None
    day_month: Optional[str] = Field(None, description="MM-JJ pour une date annuelle (« 03-31 »)")
    years_relative: list[int] = Field(default_factory=list, description="Années relatives à l'ancre (0 = N)")
    year_offset: Optional[int] = Field(None, description="Pour date_annuelle : 0 = année de l'événement, 1 = année suivante")
    window_start: Optional[str] = Field(None, description="MM-JJ début de période")
    window_end: Optional[str] = Field(None, description="MM-JJ fin de période (peut chevaucher l'année)")
    texte: str = Field("", description="Formulation exacte de l'échéance dans l'acte")


CategorieObligation = Literal["mesure", "transmission", "declaration", "contrainte_calendaire", "interdiction",
                              "gouvernance", "suivi", "foncier", "donnees", "information", "autre"]


class ObligationLLM(BaseModel):
    intitule: str = Field(description="Verbe à l'infinitif + objet, court (« Transmettre le plan de gestion détaillé »)")
    description: str = ""
    categorie: CategorieObligation
    debiteur: str = Field("beneficiaire", description="Qui doit agir : bénéficiaire, écologue, opérateur, entreprises…")
    destinataire: str = ""
    echeance: Echeance
    preuve_attendue: list[PreuveType] = []
    condition: str = Field("", description="Déclencheur si l'obligation est conditionnelle (« si un arbre gîte est abattu »)")
    mesure_code: str = ""
    quantites: list[Quantite] = []
    especes: list[str] = []
    citations: list[Citation] = []
    confidence: float = Field(0.5, ge=0, le=1)
    ambiguities: list[str] = []


class ArticleLLM(BaseModel):
    titre: str = ""
    resume: str = Field("", description="2 à 4 phrases fidèles, chiffres inclus")
    categories: list[CategorieArticle] = []
    mesures: list[Mesure] = []
    obligations: list[ObligationLLM] = []
    references_internes: list[str] = Field(default_factory=list, description="Articles de l'acte cités (« 12.4 »)")
    figures: list[str] = []


class ArticleOutLLM(ArticleLLM):
    numero: str = Field(description="Numéro de l'article tel que dans la structure détectée (ex. 11.2)")


class ArticlesBatchLLM(BaseModel):
    articles: list[ArticleOutLLM]


class SinglePassLLM(BaseModel):
    fiche: FicheLLM
    synthese: SyntheseLLM
    articles: list[ArticleOutLLM]


class Article(ArticleLLM):
    numero: str                          # « 11 », « 11.2 », « 16.1 »
    parent: Optional[str] = None
    niveau: int = 1
    page_debut: int = 0
    page_fin: int = 0
    texte: str = ""
    support: float = 1.0                 # accord entre échantillons (résumé/catégories)


# =============================================================================
# 4. Obligations à plat, échéancier, figures, contrôles
# =============================================================================
Decision = Literal["retenue", "a_valider", "a_revoir"]


class Confiance(BaseModel):
    evidence: float = 0.0               # citations retrouvées
    agreement: float = 0.0              # présence dans les k échantillons
    specificity: float = 1.0            # échéance exploitable, pas d'ambiguïté
    llm: float = 0.5
    score: float = 0.0


class Obligation(ObligationLLM):
    id: str = ""
    article: str = ""
    articles_lies: list[str] = []       # autres articles qui énoncent la même obligation
    support: float = 1.0
    confiance: Confiance = Field(default_factory=Confiance)
    decision: Decision = "a_revoir"
    issues: list[str] = []
    statut: Literal["non_evalue", "satisfaite", "en_retard", "non_satisfaite", "sans_objet"] = "non_evalue"
    preuves: list[str] = []             # documents du dossier qui la satisfont (étape ultérieure)


class EcheanceCalculee(BaseModel):
    obligation_id: str
    article: str
    intitule: str
    date: Optional[str] = None          # date calculée (ou première date d'une série)
    dates: list[str] = []               # séries (bilans N+4, N+6…)
    fenetre: str = ""                   # périodes autorisées / interdites
    libelle: str = ""                   # « 15 j avant démarrage des travaux »
    ancre: str = ""
    hypothese: str = ""                 # ancre supposée (ex. notification = signature)
    calculable: bool = False
    preuve_attendue: list[str] = []
    statut: str = "non_evalue"


class Figure(BaseModel):
    numero: str
    legende: str
    page: int
    article: Optional[str] = None
    contenu: str = ""                   # rempli par la lecture vision (optionnelle)
    valeurs: list[str] = []


class ControleLLM(BaseModel):
    sujet: str
    description: str
    gravite: Literal["alerte", "info"] = "alerte"
    articles: list[str] = []
    valeurs: list[str] = Field(default_factory=list, description="Les valeurs en conflit, une par entrée")
    citations: list[Citation] = []


class ControlesLLM(BaseModel):
    controles: list[ControleLLM]


class Controle(ControleLLM):
    origine: Literal["citation", "contradiction_regle", "contradiction_llm", "couverture", "format"] = "contradiction_llm"


class ArreteAnalyse(BaseModel):
    document: str
    sha256: str
    settings: dict
    ancres: dict[str, str] = {}         # dates d'ancrage connues (notification, démarrage…)
    fiche: FicheLLM
    synthese: Synthese
    articles: list[Article]
    obligations: list[Obligation]
    echeancier: list[EcheanceCalculee]
    figures: list[Figure]
    controles: list[Controle]
    stats: dict = {}
