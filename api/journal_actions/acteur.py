"""Auteur d'une action journalisée.

L'identité vient du JWT vérifié par le middleware. Les en-têtes
``X-Acteur`` et ``X-User-Id`` ne sont pas lus : un client pouvait
s'y attribuer le nom de quelqu'un d'autre.
"""

from __future__ import annotations

from auth.deps import get_claims


def acteur_courant() -> str | None:
    """Libellé d'audit : e-mail du jeton, sinon son sujet."""
    claims = get_claims()
    email = claims.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()[:200]
    sub = claims.get("sub")
    if sub:
        return str(sub)[:200]
    return None
