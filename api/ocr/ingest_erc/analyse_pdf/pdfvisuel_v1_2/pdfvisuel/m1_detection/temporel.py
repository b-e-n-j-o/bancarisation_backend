"""Reconnaissance des libellés temporels : mois, années, années relatives (N, N+k), plages."""
from __future__ import annotations

import re

from .geometrie import normaliser

MOIS = {
    "janvier": 1, "janv": 1, "jan": 1,
    "fevrier": 2, "fevr": 2, "fev": 2,
    "mars": 3, "mar": 3,
    "avril": 4, "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7, "juil": 7,
    "aout": 8,
    "septembre": 9, "sept": 9, "sep": 9,
    "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11,
    "decembre": 12, "dec": 12,
}
NOMS_MOIS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]
# Noms complets seulement, pour lire la prose sans faux positifs ("oct" dans un mot, etc.)
MOIS_PROSE = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
              "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
LETTRES_MOIS = "JFMAMJJASOND"

RE_ANNEE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
RE_NREL = re.compile(r"(?<![A-Za-z0-9])N\s*(?:\+\s*(\d{1,2}))?(?![A-Za-z0-9])")
RE_ANNEE_SEULE = re.compile(r"^(?:19|20)\d{2}$")
RE_NREL_SEUL = re.compile(r"^N(?:\+\d{1,2})?$")
# Colonne d'état initial placée avant N dans beaucoup de frises (« État zéro », « État initial », « T0 »)
RE_ETAT_ZERO = re.compile(r"\b(etat\s*(zero|0|initial)|t\s*0|e\s*0)\b")


def normaliser_texte(t: str) -> str:
    return " ".join(normaliser(x) for x in t.split())


def mois_du_mot(texte: str) -> int | None:
    return MOIS.get(normaliser(texte))


def jeton_temporel(texte: str) -> tuple[str, int] | None:
    """('mois', m) | ('annee', a) | ('nrel', k) | None pour un mot isolé."""
    t = texte.strip(" .,;:()")
    m = mois_du_mot(t)
    if m:
        return ("mois", m)
    if RE_ANNEE_SEULE.match(t):
        return ("annee", int(t))
    if RE_NREL_SEUL.match(t):
        return ("nrel", int(t[2:]) if "+" in t else 0)
    return None


def analyser_libelle(texte: str, type_axe: str) -> dict:
    """Interprète le libellé complet d'une colonne (éventuellement multi-lignes).

    Exemples : "N 2025" -> n 0, année 2025 ; "N+6 à N+10 2031 à 2035" -> plage ;
    "Mar s" (mot coupé par un retour à la ligne) -> mars.
    """
    res: dict = {"libelle": texte.strip()}
    if type_axe == "mois":
        m = mois_du_mot(texte.replace(" ", "")) or next(
            (mois_du_mot(x) for x in texte.split() if mois_du_mot(x)), None)
        if m:
            res["mois"] = m
        return res
    if RE_ETAT_ZERO.search(normaliser_texte(texte)):
        res["etat_zero"] = True
    annees = [int(a) for a in RE_ANNEE.findall(texte)]
    nrels = [int(k) if k else 0 for k in RE_NREL.findall(texte)]
    if annees:
        res["annee_debut"], res["annee_fin"] = annees[0], annees[-1]
    if nrels:
        res["n_debut"], res["n_fin"] = nrels[0], nrels[-1]
    res["plage"] = (len(annees) > 1 and annees[0] != annees[-1]) or (len(nrels) > 1 and nrels[0] != nrels[-1])
    return res


def est_libelle_temporel(info: dict, type_axe: str) -> bool:
    if type_axe == "mois":
        return "mois" in info
    return "annee_debut" in info or "n_debut" in info or info.get("etat_zero", False)


def mois_dans_prose(texte: str) -> set[int]:
    """Mois cités en toutes lettres dans un texte libre."""
    vus = set()
    for brut in re.findall(r"[A-Za-zÀ-ÿ]+", texte):
        m = MOIS_PROSE.get(normaliser(brut))
        if m:
            vus.add(m)
    return vus
