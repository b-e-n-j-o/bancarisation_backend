"""Chemins canoniques partagés par l'index, les PDF et les copies binaires."""

from __future__ import annotations

from pathlib import Path

from api.export.nommage import slug


def chemin_dxf(nom_fichier: str | None) -> str:
    stem = slug(Path(nom_fichier or "plan").stem, 40)
    return f"04_Geometries/cao/{stem}.dxf"


def chemin_bilan(annee: object, type_bilan: str | None, version: object) -> str:
    genre = "financiers" if type_bilan == "financier" else "ecologiques"
    libelle = "bilan-financier" if genre == "financiers" else "bilan-ecologique"
    numero = int(version or 1)
    nom = (
        f"{annee}_{libelle}_VALIDE.pdf"
        if numero <= 1
        else f"{annee}_v{numero}_{libelle}_VALIDE.pdf"
    )
    return f"10_Bilans/{genre}/{nom}"
