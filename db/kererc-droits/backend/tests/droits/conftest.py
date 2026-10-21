"""
Tests des droits sur les routes du backend, avec de vrais jetons.

Pourquoi ici et pas seulement en SQL : plusieurs routes tournent en `postgres` ou
`service_role` (module admin, accès projet, Storage). La RLS ne s'y applique pas,
seul le code Python protège. Ces tests le vérifient de l'extérieur, comme un
utilisateur malveillant le ferait avec son propre jeton.

Prérequis (une fois) :
  1. backend/db/seeds/utilisateurs_roles_test.sql  (8 rôles sur le projet [Démo] Rôles)
  2. backend/db/seeds/utilisateurs_temoins_test.sql (admin, sans-projet, intrus)
  3. backend lancé, pointant sur la même base

Variables d'environnement (jamais dans le repo) :
  KERERC_API_URL      ex. http://localhost:8000
  SUPABASE_URL        ex. https://supabase-dev.kerelia.fr
  SUPABASE_ANON_KEY
  MDP_RECETTE         même valeur que VITE_MDP_RECETTE
  RECETTE_PROJET_ID   (défaut : projet [Démo] Rôles)
  RECETTE_DOCUMENT_ID (défaut : document du seed)

Lancer :  pytest backend/tests/droits -v
Sans ces variables, tout le dossier est ignoré (skip), il ne casse pas les autres tests.

Second facteur : GoTrue délivre ici un jeton aal1. Si le backend exige aal2 un
jour, enrôler un TOTP à secret connu sur les comptes de recette et compléter
`jeton()` (pyotp), plutôt que d'ajouter un contournement côté serveur.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
import pytest

from .commun import COMPTES, DOCUMENT_DEMO, PROJET_DEMO


@dataclass
class Config:
    api: str
    supabase: str
    anon_key: str
    mdp: str
    projet: str
    document: str


@pytest.fixture(scope="session")
def config() -> Config:
    manquantes = [v for v in ("KERERC_API_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY", "MDP_RECETTE")
                  if not os.environ.get(v)]
    if manquantes:
        pytest.skip(f"tests de droits ignorés, variables absentes : {', '.join(manquantes)}")
    return Config(
        api=os.environ["KERERC_API_URL"].rstrip("/"),
        supabase=os.environ["SUPABASE_URL"].rstrip("/"),
        anon_key=os.environ["SUPABASE_ANON_KEY"],
        mdp=os.environ["MDP_RECETTE"],
        projet=os.environ.get("RECETTE_PROJET_ID", PROJET_DEMO),
        document=os.environ.get("RECETTE_DOCUMENT_ID", DOCUMENT_DEMO),
    )


@pytest.fixture(scope="session")
def jetons(config: Config):
    """jetons('contributeur') -> access_token ; mis en cache pour la session."""
    cache: dict[str, str] = {}

    def jeton(role: str) -> str:
        if role not in cache:
            r = httpx.post(
                f"{config.supabase}/auth/v1/token",
                params={"grant_type": "password"},
                headers={"apikey": config.anon_key},
                json={"email": COMPTES[role], "password": config.mdp},
                timeout=20,
            )
            if r.status_code != 200:
                pytest.fail(
                    f"connexion impossible pour {COMPTES[role]} ({r.status_code}). "
                    "Seeds lancés sur cette base ? Même mot de passe que MDP_RECETTE ?"
                )
            cache[role] = r.json()["access_token"]
        return cache[role]

    return jeton


@pytest.fixture(scope="session")
def api(config: Config, jetons):
    """api('lecteur').get(...) : client authentifié ; api(None) : sans jeton."""
    clients: dict[str | None, httpx.Client] = {}

    def client(role: str | None) -> httpx.Client:
        if role not in clients:
            entetes = {"Authorization": f"Bearer {jetons(role)}"} if role else {}
            clients[role] = httpx.Client(base_url=config.api, headers=entetes, timeout=30)
        return clients[role]

    yield client
    for c in clients.values():
        c.close()


@pytest.fixture(scope="session")
def org_projet(config: Config, api) -> str:
    """Organisation propriétaire du projet de recette, lue par l'admin."""
    r = api("admin").get(f"/api/projets/{config.projet}")
    if r.status_code != 200:
        pytest.fail(f"l'admin ne lit pas le projet de recette ({r.status_code}) : seeds témoins lancés ?")
    org = r.json().get("organisation_id")
    assert org, "la réponse de GET /api/projets/{id} ne contient pas organisation_id"
    return org
