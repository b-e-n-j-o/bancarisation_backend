"""Planning pluriannuel : une ligne par action, une colonne par année."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from api.export.pdf.rendu import rendre_pdf

_COULEURS = {
    "realise": ("#15803d", "#ffffff"),
    "planifie": ("#1d4ed8", "#ffffff"),
    "en_cours": ("#0369a1", "#ffffff"),
    "en_retard": ("#b45309", "#ffffff"),
    "a_confirmer": ("#a16207", "#ffffff"),
    "repousse": ("#9a3412", "#ffffff"),
}
_PRIORITE = ["en_retard", "a_confirmer", "en_cours", "planifie", "realise", "repousse", "archive"]


def decennies(annee_min: int, annee_max: int) -> list[list[int]]:
    pages = []
    debut = (annee_min // 10) * 10
    while debut <= annee_max:
        annees = [annee for annee in range(debut, debut + 10) if annee_min <= annee <= annee_max]
        if annees:
            pages.append(annees)
        debut += 10
    return pages


def pages_du_planning(actions: list[dict[str, Any]], aujourd_hui: date | None = None) -> list[dict[str, Any]]:
    if not actions:
        return []
    jour = aujourd_hui or date.today()
    annees = sorted({int(a["annee"]) for a in actions if a.get("annee")})
    if not annees:
        return []
    regroupes: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for action in actions:
        cle = (str(action.get("mesure") or "—"), str(action.get("titre") or "Action"))
        regroupes[cle].append(action)
    pages = []
    for tranche in decennies(annees[0], annees[-1]):
        lignes = []
        for (mesure, titre), groupe in sorted(regroupes.items()):
            cellules = []
            for annee in tranche:
                statuts = [_statut_affiche(a, jour) for a in groupe if int(a.get("annee") or 0) == annee]
                if not statuts:
                    cellules.append({"texte": "", "couleur": "transparent", "encre": "#1c241c"})
                    continue
                dominant = _dominant(statuts)
                fond, encre = _COULEURS.get(dominant, ("#e5e7eb", "#1c241c"))
                texte = str(len(statuts)) if len(statuts) > 1 else ""
                cellules.append({"texte": texte, "couleur": fond, "encre": encre})
            lignes.append({"mesure": mesure, "action": titre, "cellules": cellules})
        pages.append({
            "titre": f"{tranche[0]} – {tranche[-1]}",
            "annees": tranche,
            "lignes": lignes,
        })
    return pages


def ecrire_planning(destination: Path, projet_nom: str, genere_le: str, actions: list[dict]) -> None:
    rendre_pdf(
        "planning.html.j2",
        {
            "titre": "Planning pluriannuel",
            "projet_nom": projet_nom,
            "genere_le": genere_le,
            "page_size": "A3 landscape",
            "pages": pages_du_planning(actions),
        },
        destination,
    )


def _statut_affiche(action: dict, jour: date) -> str:
    statut = str(action.get("statut") or "")
    if statut in {"realise", "supprime", "archive"}:
        return statut
    fin = action.get("fin")
    if isinstance(fin, date) and fin < jour:
        return "en_retard"
    return statut or "planifie"


def _dominant(statuts: list[str]) -> str:
    comptes = Counter(statuts)
    return max(
        comptes,
        key=lambda statut: (comptes[statut], -_PRIORITE.index(statut) if statut in _PRIORITE else -99),
    )
