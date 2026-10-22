"""Jeu de données commun à l'archive v1.1 et au modèle client."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FichierImport:
    document_id: str
    chemin_local: Path
    nom: str
    categorie: str
    sha256: str


@dataclass
class JeuDonnees:
    tables: dict[str, list[dict]]
    fichiers: dict[str, dict] = field(default_factory=dict)
    avertissements: list[str] = field(default_factory=list)
    conserver_ids: bool = False
    source: str = "archive"
    meta: dict = field(default_factory=dict)
