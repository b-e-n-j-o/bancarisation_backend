"""Constats d'analyse du modèle client."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass
class Constat:
    niveau: Literal["erreur", "avertissement", "info"]
    onglet: str | None
    ligne: int | None
    colonne: str | None
    message: str

    def pour_json(self) -> dict:
        return asdict(self)
