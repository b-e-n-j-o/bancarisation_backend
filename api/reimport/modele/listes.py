"""Listes du modèle. Les rôles peuvent être remplacés par le référentiel en base."""

from __future__ import annotations

PROCEDURES = [
    "Dérogation espèces",
    "IOTA – Autorisation",
    "IOTA – Déclaration",
    "ICPE - Autorisation",
    "ICPE – Déclaration",
    "Autorisation environnementale",
    "Autorisation défrichement",
    "Autorisation en espace protégé",
    "Déclaration de projet",
    "Autorisation unique IOTA - expérimentation",
    "Autorisation unique ICPE - expérimentation",
    "Permis de construire",
    "Autre",
]

DISPOSITIFS = [
    ("obligation", "Obligation de compensation"),
    ("site_credits", "Site de crédits"),
]

ROLES = [
    ("maitre_ouvrage", "Maître d'ouvrage"),
    ("maitre_ouvrage_delegue", "Maître d'ouvrage délégué"),
    ("charge_mission_moa", "Chargé de mission MOA"),
    ("operateur_compensation", "Opérateur de compensation"),
    ("charge_projet", "Chargé de projet"),
    ("ecologue_referent", "Écologue référent"),
    ("proprietaire", "Propriétaire"),
    ("preneur_bail", "Preneur à bail"),
    ("gestionnaire_site", "Gestionnaire de site"),
    ("prestataire_travaux", "Prestataire travaux"),
    ("prestataire_suivi", "Prestataire de suivi"),
    ("bureau_etudes", "Bureau d'études"),
    ("dreal", "DREAL"),
    ("ddt", "DDT / DDTM"),
    ("autre", "Autre"),
]

CATEGORIES_DOC = [
    "arrete", "plan_gestion", "suivi_travaux", "cerfa", "sig",
    "bilans_annuels", "foncier", "financier", "autre",
]

ALIAS_CATEGORIES = {
    "arretes": "arrete",
    "arrete": "arrete",
    "plans_de_gestion": "plan_gestion",
    "plan_de_gestion": "plan_gestion",
    "plan_gestion": "plan_gestion",
    "suivi": "suivi_travaux",
    "suivi_travaux": "suivi_travaux",
    "financier": "financier",
    "financiers": "financier",
    "foncier": "foncier",
    "sig": "sig",
    "cerfa": "cerfa",
    "bilans": "bilans_annuels",
    "bilans_annuels": "bilans_annuels",
    "autre": "autre",
}

EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".odt", ".ods", ".jpg", ".jpeg", ".png",
    ".tif", ".tiff", ".dxf", ".dwg", ".zip", ".csv", ".txt", ".geojson",
}

VERSION_MODELE = "1"


def libelles_thema() -> list[str]:
    from api.ocr.extractions.catalogue.thema import codes_thema
    return sorted(codes_thema()) + ["autre"]
