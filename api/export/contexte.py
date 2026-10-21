"""Identité d'un export et connexion métier sous RLS."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_backend_database_url
from api.db.utilisateur import connect_utilisateur


@dataclass
class ExportOptions:
    inclure_budget: bool = True
    inclure_documents: bool = True
    annee_min: int | None = None
    annee_max: int | None = None
    inclure_supprimees: bool = False
    conservation_jours: int = 7

    @classmethod
    def depuis_json(cls, brut: dict[str, Any] | None) -> ExportOptions:
        data = brut or {}
        jours = data.get("conservation_jours") or 7
        return cls(
            inclure_budget=bool(data.get("inclure_budget", True)),
            inclure_documents=bool(data.get("inclure_documents", True)),
            annee_min=_annee(data.get("annee_min")),
            annee_max=_annee(data.get("annee_max")),
            inclure_supprimees=bool(data.get("inclure_supprimees", False)),
            conservation_jours=max(1, int(jours)),
        )

    def pour_json(self) -> dict[str, Any]:
        return {
            "inclure_budget": self.inclure_budget,
            "inclure_documents": self.inclure_documents,
            "annee_min": self.annee_min,
            "annee_max": self.annee_max,
            "inclure_supprimees": self.inclure_supprimees,
            "conservation_jours": self.conservation_jours,
        }


def _annee(valeur: Any) -> int | None:
    if valeur is None or valeur == "":
        return None
    return int(valeur)


@dataclass
class FichierExport:
    chemin_archive: str
    chemin_local: Path
    module: str
    description: str
    nb_elements: int | None = None
    compresser: bool = True


@dataclass
class ExportContexte:
    export_id: UUID
    projet_id: UUID
    user_id: UUID
    organisation_id: UUID
    options: ExportOptions
    dossier_tmp: Path
    url_front: str
    racine_zip: str = ""
    avertissements: list[str] = field(default_factory=list)
    chemins_pris: set[str] = field(default_factory=set)
    # document_id -> chemin dans le ZIP, mis à jour si le nom est dédoublonné
    alias_fichiers: dict[str, str] = field(default_factory=dict)

    def avertir(self, message: str) -> None:
        self.avertissements.append(message)

    def claims(self) -> dict[str, str]:
        return {"sub": str(self.user_id), "role": "authenticated"}


@contextmanager
def connect_backend() -> Iterator[psycopg.Connection]:
    """kererc_backend sans SET ROLE : journal d'export et purge uniquement."""
    conn = psycopg.connect(get_backend_database_url(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def connect_export(ctx: ExportContexte) -> Iterator[psycopg.Connection]:
    """Lecture des données projet avec l'identité du demandeur (RLS)."""
    with connect_utilisateur(ctx.claims(), row_factory=dict_row) as conn:
        yield conn
