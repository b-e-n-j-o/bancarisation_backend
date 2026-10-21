"""Fiche projet PDF. Les montants passent par lecture_export."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from api.export.pdf.rendu import rendre_pdf


def contexte_fiche(
    projet_nom: str,
    genere_le: str,
    organisation: dict,
    partenaires: list[dict],
    mesures: list[dict],
    unites: list[dict],
    identite: list[tuple[str, Any]],
    chiffres: list[tuple[str, Any]],
) -> dict[str, Any]:
    return {
        "titre": "Fiche projet",
        "projet_nom": projet_nom,
        "genere_le": genere_le,
        "page_size": "A4",
        "organisation": organisation,
        "partenaires": partenaires,
        "mesures": mesures,
        "unites": unites,
        "identite": identite,
        "chiffres": chiffres,
    }


def ecrire_fiche(destination: Path, contexte: dict[str, Any]) -> None:
    rendre_pdf("fiche_projet.html.j2", contexte, destination)
