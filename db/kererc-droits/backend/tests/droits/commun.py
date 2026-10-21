"""Constantes et aides partagées par les tests de droits."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

PROJET_DEMO = "00000000-0000-4000-8000-0000000000d1"
DOCUMENT_DEMO = "00000000-0000-4000-8000-0000000000e1"

# Comptes du seed. La clé est le nom utilisé dans les tests.
COMPTES = {
    "gestionnaire": "gestionnaire@roles.test",
    "contributeur": "contributeur@roles.test",
    "lecteur": "lecteur@roles.test",
    "operateur": "operateur@roles.test",
    "instructeur": "instructeur@roles.test",
    "partenaire": "partenaire@roles.test",
    "moa": "moa@roles.test",
    "deposant": "deposant@roles.test",
    "admin": "admin@roles.test",
    "sans_projet": "sans-projet@roles.test",
    "intrus": "intrus@roles.test",
}

PARTENAIRES = ["operateur", "instructeur", "partenaire", "moa", "deposant"]
SANS_ACCES = ["sans_projet", "intrus"]

# Routes : noms à confirmer dans le code (backend/api/*/router.py). Garder le principe.
ROUTES = {
    "membres": "/api/organisations/{org}/membres",
    "invitations": "/api/organisations/{org}/invitations",
    "acces": "/api/projets/{projet}/acces",
    "acces_ligne": "/api/projets/{projet}/acces/{acces}",
    "document_url": "/api/documents/{document}/url",
    "document_depot": "/api/projets/{projet}/documents",
    "document": "/api/documents/{document}",
}

# Un refus correct : 403 (pas le droit) ou 404 (ligne invisible, la règle du lot 2).
REFUS = {403, 404}


def sub(jeton: str) -> str:
    """Identifiant utilisateur lu dans le JWT (sans vérifier la signature : test seulement)."""
    charge = jeton.split(".")[1]
    charge += "=" * (-len(charge) % 4)
    return json.loads(base64.urlsafe_b64decode(charge))["sub"]


def assert_refus(r: httpx.Response, *, aussi: set[int] = frozenset()) -> None:
    """Refus attendu. Un 422 veut dire que le test envoie un corps invalide : c'est le test qu'il faut corriger."""
    if r.status_code == 422:
        pytest.fail(f"422 : corps de requête invalide, le test ne vérifie rien. Réponse : {r.text[:300]}")
    assert r.status_code in REFUS | set(aussi), (
        f"{r.request.method} {r.request.url.path} : attendu un refus {sorted(REFUS | set(aussi))}, "
        f"obtenu {r.status_code} — {r.text[:300]}"
    )
