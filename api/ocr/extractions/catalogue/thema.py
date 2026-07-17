"""
thema.py — Catalogue Théma (nomenclature officielle des mesures compensatoires).

Charge ``thema.json`` pour le prompt d'extraction et la validation de ``lib_thema``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOGUE_PATH = Path(__file__).resolve().parent / "thema.json"

LIB_THEMA_AUTRE = "autre"


@lru_cache(maxsize=1)
def charger_catalogue() -> dict[str, Any]:
    return json.loads(_CATALOGUE_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def codes_thema() -> frozenset[str]:
    codes: set[str] = set()
    for famille in charger_catalogue().get("familles", []):
        for groupe in famille.get("groupes", []):
            for mesure in groupe.get("mesures", []):
                code = str(mesure.get("code", "")).strip()
                if code:
                    codes.add(code)
    return frozenset(codes)


def formater_catalogue_pour_prompt() -> str:
    """Liste compacte ``code — intitulé`` pour injection dans le prompt LLM."""
    lignes: list[str] = []
    for famille in charger_catalogue().get("familles", []):
        fam_code = famille.get("code", "")
        fam_label = famille.get("label", "")
        lignes.append(f"\nFamille {fam_code} — {fam_label}")
        for groupe in famille.get("groupes", []):
            grp_label = groupe.get("label", "")
            lignes.append(f"  [{grp_label}]")
            for mesure in groupe.get("mesures", []):
                code = mesure.get("code", "")
                intitule = mesure.get("intitule", "")
                lignes.append(f"    {code} — {intitule}")
    return "\n".join(lignes).strip()


def normaliser_lib_thema(valeur: str | None) -> str:
    """Retourne un code catalogue valide, sinon ``autre``."""
    if valeur is None:
        return LIB_THEMA_AUTRE
    code = str(valeur).strip()
    if not code or code.lower() in {LIB_THEMA_AUTRE, "n/a", "na", "none", "null"}:
        return LIB_THEMA_AUTRE
    if code in codes_thema():
        return code
    return LIB_THEMA_AUTRE
