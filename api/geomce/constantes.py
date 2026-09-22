"""Constantes GéoMCE — gabarit v2.2 (notice Cerema / MTE).

DBF_SCHEMA est appliqué à l'écriture shapefile (pyshp). À confronter une fois
au gabarit_light.dbf officiel via pyogrio.read_info().
"""

from __future__ import annotations

# Ordre et définitions des champs DBF (écriture shapefile)
# type: 'C' = texte, 'N' = numérique
DBF_SCHEMA: list[dict[str, object]] = [
    {"name": "ID", "type": "N", "width": 10, "precision": 0},
    {"name": "NOM", "type": "C", "width": 50, "precision": 0},
    {"name": "CIBLE", "type": "C", "width": 100, "precision": 0},
    {"name": "DESCRIPTIO", "type": "C", "width": 254, "precision": 0},
    {"name": "DECISION", "type": "C", "width": 254, "precision": 0},
    {"name": "REFEI", "type": "C", "width": 254, "precision": 0},
    {"name": "CATEGORIE", "type": "C", "width": 7, "precision": 0},
]

DBF_FIELD_NAMES = [f["name"] for f in DBF_SCHEMA]
DBF_WIDTHS = {str(f["name"]): int(f["width"]) for f in DBF_SCHEMA}

# Vocabulaire fermé CIBLE — verbatim notice (sans accents volontaires)
CIBLES_FERMEES: tuple[str, ...] = (
    "Population",
    "Faune et flore",
    "Habitats naturels",
    "Sites et paysages",
    "Biens matériels",
    "Continuités écologiques",
    "Equilibres biologiques",
    "Facteurs climatiques",
    "Patrimoine culturel et archéologique",
    "Sol",
    "Eau",
    "Air",
    "Bruit",
    "Espaces naturels, agricoles, forestiers, maritimes ou de loisirs",
    "Cible a preciser",
)

CIBLE_SEP = "|"  # sans espace

# SCR par territoire (département → EPSG) — SCR d'export
SRID_METROPOLE = 2154
SRID_PAR_DEPT: dict[str, int] = {
    # La Réunion
    "974": 2975,
    # Guadeloupe / Martinique (RGAF09 / UTM 20N)
    "971": 5490,
    "972": 5490,
    # Guyane (SCR d'export ; l'aire se calcule en 2972)
    "973": 3857,
    # Mayotte
    "976": 4471,
}

SRID_LABELS: dict[int, str] = {
    2154: "RGF93 / Lambert-93 — EPSG:2154",
    2975: "RGR92 / UTM 40S — EPSG:2975",
    5490: "RGAF09 / UTM 20N — EPSG:5490",
    3857: "WGS 84 / Pseudo-Mercator — EPSG:3857",
    4471: "RGM04 / UTM 38S — EPSG:4471",
}

# SCR pour le calcul de surface affiché (l'export reste dans SRID_PAR_DEPT).
# Pseudo-Mercator déforme l'aire (~0,5 % à 4° N) : UTM 22N pour la Guyane.
SRID_AIRE: dict[int, int] = {
    3857: 2972,
}

DEPTS_DOM: frozenset[str] = frozenset({"971", "972", "973", "974", "975", "976"})

# Stratégie géométrique documentée par la notice (une ligne par polygone,
# attributs identiques). « multipart » existe en API mais n'est pas dans la notice.
STRATEGIE_GEOM_DEFAUT = "eclate"

CHAMP_VIDE = "-"
ENCODING = "UTF-8"
NOM_FICHIER_MAX = 60
