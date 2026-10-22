"""Normalisation des cellules du modèle (codes, mois, nombres, dates, statuts)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from api.ocr.domain.ug_ids import normalize_ug_id

MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}
MOIS_LIBELLES = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

STATUTS = {
    "a confirmer": "a_confirmer",
    "à confirmer": "a_confirmer",
    "planifiee": "planifie",
    "planifiée": "planifie",
    "en cours": "en_cours",
    "realisee": "realise",
    "réalisée": "realise",
    "repoussee": "repousse",
    "repoussée": "repousse",
    "a_confirmer": "a_confirmer",
    "planifie": "planifie",
    "en_cours": "en_cours",
    "realise": "realise",
    "repousse": "repousse",
}

RECURRENCES = {
    "ponctuelle": "ponctuelle",
    "annuelle": "annuelle",
    "tous les n ans": "tous_les_n",
    "annees listees": "annees",
    "années listées": "annees",
}

TYPES_UG = {
    "surfacique": "surf",
    "lineaire": "lin",
    "linéaire": "lin",
    "ponctuelle": "pct",
}


def _pli(valeur: str) -> str:
    texte = unicodedata.normalize("NFD", valeur.strip().lower())
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")


def texte(valeur) -> str:
    if valeur is None:
        return ""
    return str(valeur).strip()


def code(valeur) -> str:
    return re.sub(r"\s+", "", texte(valeur)).upper()


def code_ug(valeur) -> str | None:
    return normalize_ug_id(texte(valeur))


def mois(valeur) -> int | None:
    brut = texte(valeur)
    if not brut:
        return None
    if brut.isdigit():
        n = int(brut)
        return n if 1 <= n <= 12 else None
    return MOIS.get(_pli(brut))


def nombre(valeur) -> float | None:
    brut = texte(valeur).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not brut:
        return None
    try:
        return float(brut)
    except ValueError:
        return None


def annee(valeur) -> int | None:
    n = nombre(valeur)
    if n is None:
        return None
    entier = int(n)
    return entier if entier == n and 1900 <= entier <= 2200 else None


def date_fr(valeur) -> date | None:
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    brut = texte(valeur)
    if not brut:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(brut, fmt).date()
        except ValueError:
            continue
    return None


def statut(valeur) -> str | None:
    brut = texte(valeur)
    if not brut:
        return None
    return STATUTS.get(brut) or STATUTS.get(_pli(brut)) or STATUTS.get(brut.lower())


def recurrence(valeur) -> str | None:
    brut = texte(valeur)
    if not brut:
        return None
    return RECURRENCES.get(_pli(brut)) or RECURRENCES.get(brut.lower())


def type_ug(valeur) -> str | None:
    brut = texte(valeur)
    if not brut:
        return None
    return TYPES_UG.get(_pli(brut)) or TYPES_UG.get(brut.lower())


def liste_codes(valeur, ug: bool = False) -> list[str]:
    brut = texte(valeur)
    if not brut:
        return []
    morceaux = re.split(r"[;,\n]", brut)
    sortie = []
    for morceau in morceaux:
        propre = code_ug(morceau) if ug else code(morceau)
        if propre:
            sortie.append(propre)
    return sortie
