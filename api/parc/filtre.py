"""Filtrage d'habilitation pour les requêtes parc (chemin psycopg)."""

from __future__ import annotations

from uuid import UUID


def filtre_organisation(
    role: str,
    organisation_id: UUID | None,
) -> tuple[str, list]:
    """Clause WHERE d'habilitation à injecter dans toute requête de parc.

    Controleur / admin → tout le parc.
    Operateur → uniquement son organisation.
    Operateur sans organisation → rien (sécurité par défaut).
    """
    if role in ("controleur", "admin"):
        return "TRUE", []
    if organisation_id is None:
        return "FALSE", []
    return "organisation_id = %s", [str(organisation_id)]
