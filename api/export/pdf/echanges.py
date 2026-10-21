"""PDF du fil et des notes, à partir des données déjà chargées pour le JSON."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from api.export.pdf.rendu import rendre_pdf


def ecrire_fil(destination: Path, projet_nom: str, genere_le: str, charge: dict, alias: dict[str, str]) -> None:
    demandes = []
    for bloc in charge.get("demandes") or []:
        messages = []
        for message in bloc.get("messages") or []:
            pieces = []
            for identifiant in message.get("pieces") or []:
                chemin = alias.get(str(identifiant))
                if chemin:
                    pieces.append(chemin)
            messages.append({**message, "pieces_chemins": pieces})
        demandes.append({**bloc, "messages": messages})
    rendre_pdf(
        "fil_echanges.html.j2",
        {
            "titre": "Fil du projet",
            "projet_nom": projet_nom,
            "genere_le": genere_le,
            "fil": charge.get("fil_projet") or [],
            "demandes": demandes,
        },
        destination,
    )


def ecrire_notes(destination: Path, projet_nom: str, genere_le: str, charge: dict) -> None:
    rendre_pdf(
        "notes_actions.html.j2",
        {
            "titre": "Notes par action",
            "projet_nom": projet_nom,
            "genere_le": genere_le,
            "groupes": groupes_notes(charge.get("notes") or []),
        },
        destination,
    )


def groupes_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    par_action: dict[str, list] = defaultdict(list)
    globales = []
    for note in notes:
        if note.get("action_id"):
            par_action[str(note.get("mesure") or "Mesure")].append(note)
        else:
            globales.append(note)
    groupes = [{"titre": mesure, "notes": lignes} for mesure, lignes in sorted(par_action.items())]
    if globales:
        groupes.append({"titre": "Notes du projet", "notes": globales})
    return groupes
