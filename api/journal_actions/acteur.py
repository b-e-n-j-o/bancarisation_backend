"""Auteur d'une action journalisée.

L'identité vient du JWT vérifié par le middleware. Les en-têtes
``X-Acteur`` et ``X-User-Id`` ne sont pas lus : un client pouvait
s'y attribuer le nom de quelqu'un d'autre.
"""

from __future__ import annotations

from auth.deps import jwt_claims


def acteur_courant() -> str | None:
    """Identifiant d'audit : sujet du JWT (uuid), comme journal_audit.acteur_id.

    Sans session (satellite, tâche planifiée, clé service) : None.
    Ce n'est pas une erreur : le journal enregistre alors un auteur absent.
    L'e-mail n'est pas une clé.
    """
    claims = jwt_claims.get()
    if not claims:
        return None
    sub = claims.get("sub")
    if not sub:
        return None
    return str(sub)[:200]
