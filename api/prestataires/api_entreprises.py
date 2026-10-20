"""Recherche d'entreprises (API Recherche Entreprises, data.gouv).

Une seule requête `q` accepte un nom, un SIREN ou un SIRET.
On ne retient que l'identité, le siège, le code NAF et la TVA.
"""

from __future__ import annotations

from typing import Any

import requests

from .naf import libelle_naf

API_URL = "https://recherche-entreprises.api.gouv.fr/search"


def rechercher(q: str, *, limit: int = 8) -> list[dict[str, Any]]:
    terme = (q or "").strip()
    if len(terme) < 2:
        return []

    response = requests.get(
        API_URL,
        params={"q": terme, "per_page": max(1, min(limit, 25)), "page": 1},
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()

    fiches: list[dict[str, Any]] = []
    for row in payload.get("results") or []:
        if row.get("etat_administratif") != "A":
            continue
        siege = row.get("siege") or {}
        tva_list = row.get("tva") or []
        code = row.get("activite_principale")
        fiches.append(
            {
                "nom": row.get("nom_complet") or row.get("nom_raison_sociale"),
                "siren": row.get("siren"),
                "siret": siege.get("siret"),
                "adresse": siege.get("adresse"),
                "code_postal": siege.get("code_postal"),
                "commune": siege.get("libelle_commune"),
                "departement": siege.get("departement"),
                "code_naf": code,
                "libelle_naf": libelle_naf(code),
                "tva": tva_list[0] if tva_list else None,
            }
        )
    return fiches
