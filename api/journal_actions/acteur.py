"""Auteur d'une action journalisée.

L'identité vient du JWT vérifié par le middleware. Les en-têtes
``X-Acteur`` et ``X-User-Id`` ne sont pas lus : un client pouvait
s'y attribuer le nom de quelqu'un d'autre.
"""

from __future__ import annotations

from auth.deps import get_claims


def acteur_courant() -> str | None:
    """Identifiant d'audit : sujet du JWT (uuid), comme journal_audit.acteur_id.

    L'e-mail n'est pas une clé : il peut changer. L'affichage le résout via profils.
    """
    sub = get_claims().get("sub")
    if not sub:
        return None
    return str(sub)[:200]
