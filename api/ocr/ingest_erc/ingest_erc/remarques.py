"""Traitement des remarques produites par le LLM lors de la cartographie.

Le modèle repère souvent de vraies incohérences du dossier (numéro d'arrêté qui change,
années de fin contradictoires, objectifs qui ne correspondent pas au tableau de synthèse).
Ce module les trie en trois destinations :

  question  : la remarque porte sur un champ du référentiel -> on demande au BE de trancher
              (sauf si une règle déterministe a déjà posé la question sur ce champ) ;
  anomalie  : erreur du dossier sans effet sur le référentiel -> panneau « points relevés » ;
  interne   : bruit de notre propre chaîne de lecture (OCR) -> logs uniquement.

Une remarque non vérifiable (citation introuvable) est écartée : comme le reste de la carte,
elle ne compte que si elle est prouvée.
"""
from __future__ import annotations

import re

from .inventaire import norm
from .modeles import Question, Source

# champ du référentiel -> mots qui le désignent dans une remarque
CHAMPS = {
    "reference_decision": ("numero d arrete", "arrete n", "reference de l arrete", "n° d arrete"),
    "annee_fin": ("annee de fin", "fin du plan", "fin de gestion", "echeance du plan"),
    "annee_etat_zero": ("etat zero", "annee 0", "annee initiale"),
    "annee_N": ("annee n", "premiere annee de gestion"),
    "duree_ans": ("duree du plan", "duree de gestion"),
    "convention_montants": ("ht", "ttc", "tva"),
}
MOTS_OCR = ("ocr", "coquille", "conversion", "reconnaissance", "illisible", "bruite", "bruité")
SEUIL_FIABILITE = 0.8


def _valeurs(texte: str) -> list[str]:
    """Valeurs candidates citées dans la remarque : années, numéros d'arrêté, montants."""
    v = re.findall(r"[«'\"]\s*(n°\s*[\w/.-]+|[\w/.-]*\d[\w/.-]*)\s*[»'\"]", texte)
    if not v:
        v = re.findall(r"\b(?:19|20)\d\d\b", texte)
    return list(dict.fromkeys(x.strip() for x in v))[:6]


def classer(rem, pages_peu_fiables: set) -> str:
    """Type de la remarque : celui annoncé par le modèle, sinon déduit."""
    if rem.type in ("valeur_du_projet", "incoherence_document", "qualite_de_lecture"):
        t = rem.type
    else:
        t = "incoherence_document"
    n = norm(rem.texte)
    if any(m in n for m in MOTS_OCR) or (rem.page and rem.page in pages_peu_fiables):
        return "qualite_de_lecture"
    if t == "incoherence_document" and champ_vise(rem):
        return "valeur_du_projet"
    return t


def champ_vise(rem) -> str | None:
    n = norm(rem.texte)
    for champ, mots in CHAMPS.items():
        if any(m in n for m in mots):
            return champ
    return None


def trier(carte, doc, referentiel) -> dict:
    """Range les remarques de la carte. Ajoute les questions manquantes au référentiel et
    renvoie les listes à afficher (anomalies) et à journaliser (internes)."""
    peu_fiables = {p.num for p in doc.pages
                   if p.fiabilite_ocr is not None and p.fiabilite_ocr < SEUIL_FIABILITE}
    deja = {q.portee for q in referentiel.questions}
    anomalies, internes, questions = [], [], []

    for rem in carte.remarques:
        if not rem.verifiee:
            internes.append({"texte": rem.texte, "page": rem.page, "motif": "citation introuvable"})
            continue
        t = classer(rem, peu_fiables)
        item = {"texte": rem.texte, "page": rem.page, "citation": rem.citation, "type": t,
                "source": doc.nom}
        if t == "qualite_de_lecture":
            internes.append(item)
        elif t == "valeur_du_projet":
            champ = champ_vise(rem)
            if champ and champ in deja:
                internes.append({**item, "motif": f"question déjà posée sur {champ}"})
                continue
            opts = _valeurs(rem.texte)
            cellule = referentiel.projet.get(champ) if champ else None
            prop = str(cellule.valeur) if cellule and str(cellule.valeur) in opts else (opts[0] if opts else None)
            q = Question(
                id=f"R{len(questions) + 1}", portee=champ or "documents",
                texte="Le plan de gestion se contredit. " + rem.texte + " Quelle valeur retenir ?",
                options=opts, proposition=prop,
                sources=[Source(doc=doc.nom, loc=f"p.{rem.page}" if rem.page else "carte",
                                extrait=rem.citation or rem.texte[:120])])
            questions.append(q)
            if champ:
                deja.add(champ)
        else:
            anomalies.append(item)

    referentiel.questions += questions
    referentiel.anomalies = anomalies
    referentiel.stats["questions"] = len(referentiel.questions)
    referentiel.stats["anomalies"] = len(anomalies)
    return {"questions": len(questions), "anomalies": anomalies, "internes": internes}
