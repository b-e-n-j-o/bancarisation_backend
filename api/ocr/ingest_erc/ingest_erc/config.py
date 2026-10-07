"""Configuration OCR / LLM de ingest_erc.

Un seul endroit pour les modèles : le tableau ROLES. Changer un défaut ici
suffit pour un test A/B. Une variable d'environnement du même rôle le surcharge.
`--model` / `MISTRAL_MODEL` s'applique à tous les rôles LLM sans surcharge dédiée.

Rien n'est figé à l'import : on relit l'environnement à chaque appel.
"""
from __future__ import annotations

import os
from pathlib import Path

# Alias CLI / env → id API Mistral (aligné sur tests/smoke_test_mistral.py).
ALIAS_LLM = {
    "small": "mistral-small-latest",
    "medium": "mistral-medium-3-5",
    "large": "mistral-large-latest",
    "glm": "zai-glm-5-2",
}
NORMALISATION_LLM = {
    "mistral-medium-3.5": "mistral-medium-3-5",
    "mistral-medium-3_5": "mistral-medium-3-5",
    "mistral-medium-latest": "mistral-medium-3-5",
    "glm-5-2": "zai-glm-5-2",
    "glm-5.2": "zai-glm-5-2",
    "zai-glm-5.2": "zai-glm-5-2",
    "glm-5-3": "zai-glm-5-3",
    "glm-5.3": "zai-glm-5-3",
    "zai-glm-5.3": "zai-glm-5-3",
}

# ---------------------------------------------------------------------------
# Défauts à ajuster pour les tests. env_* = surcharge optionnelle.
# ---------------------------------------------------------------------------
# OCR 4 : mistral-ocr-latest pointe vers OCR 4 depuis le 23/06/2026.
OCR_MODELE_DEFAUT = "mistral-ocr-latest"
LLM_DEFAUT = "glm"            # → zai-glm-5-2
LLM_REPLI = "mistral-medium-3-5"  # si GLM hors palier de la clé
EFFORT_DEFAUT = "medium"
EFFORT_ROUTEUR_DEFAUT = "low"

ROLES: dict[str, dict] = {
    "ocr": {
        "usage": "Lecture PDF (Mistral OCR 4). Pas un LLM.",
        "type": "ocr",
        "modele": OCR_MODELE_DEFAUT,
        "env_modele": "MISTRAL_OCR_MODEL",
    },
    "carte": {
        "usage": "Passe 1 — structure du plan (vocabulaire, fiches, tableaux).",
        "modele": LLM_DEFAUT,
        "effort": EFFORT_DEFAUT,
        "env_modele": "MISTRAL_MODEL_CARTE",
        "env_effort": "MISTRAL_REASONING_EFFORT_CARTE",
        "budget_tokens": 100_000,
        "max_tokens": 64_000,
    },
    "routeur": {
        "usage": "Passe 1 — choix des sections PDF si la carte n'a pas suffi.",
        "modele": LLM_DEFAUT,
        "effort": "low",
        "env_modele": "MISTRAL_MODEL_ROUTEUR",
        "env_effort": "MISTRAL_REASONING_EFFORT_ROUTEUR",
        "herite_effort_global": False,
        "max_tokens": 2_000,
    },
    "referentiel": {
        "usage": "Passe 1 — extraction LLM des extraits PDF (seulement sans carte).",
        "modele": LLM_DEFAUT,
        "effort": "low",
        "env_modele": "MISTRAL_MODEL_REFERENTIEL",
        "env_effort": "MISTRAL_REASONING_EFFORT_REFERENTIEL",
    },
    "arrete": {
        "usage": "Passe 1 — lecture de la décision (identité, obligations, prescriptions).",
        "modele": LLM_DEFAUT,
        "effort": "low",
        "env_modele": "MISTRAL_MODEL_ARRETE",
        "env_effort": "MISTRAL_REASONING_EFFORT_ARRETE",
        "max_tokens": 32_000,
    },
    "carte_classeur": {
        "usage": "Passe 1 — rôle et colonnes des tableaux Excel.",
        "modele": LLM_DEFAUT,
        "effort": EFFORT_DEFAUT,
        "env_modele": "MISTRAL_MODEL_CARTE_CLASSEUR",
        "env_effort": "MISTRAL_REASONING_EFFORT_CARTE_CLASSEUR",
        "max_tokens": 64_000,
    },
    "fiche": {
        "usage": "Passe 3 — temporalité extraite des fiches-actions PDF (lots).",
        "modele": LLM_DEFAUT,
        "effort": "medium",  # high
        "env_modele": "MISTRAL_MODEL_FICHE",
        "env_effort": "MISTRAL_REASONING_EFFORT_FICHE",
        "max_tokens": 64_000,
    },
    "libelles": {
        "usage": "Passe 3 — correspondance libellés tableur → codes du référentiel.",
        "modele": LLM_DEFAUT,
        "effort": EFFORT_ROUTEUR_DEFAUT,
        "env_modele": "MISTRAL_MODEL_LIBELLES",
        "env_effort": "MISTRAL_REASONING_EFFORT_LIBELLES",
        "herite_effort_global": False,
        "max_tokens": 2_000,
    },
    # Anciens alias passe 3 : conservés pour A/B, non appelés par le code actuel.
    "calendrier": {
        "usage": "Passe 3 (non branchée) — matrices de planning.",
        "modele": LLM_DEFAUT,
        "effort": EFFORT_DEFAUT,
        "env_modele": "MISTRAL_MODEL_CALENDRIER",
        "env_effort": "MISTRAL_REASONING_EFFORT_CALENDRIER",
        "branche": False,
    },
    "budget": {
        "usage": "Passe 3 (non branchée) — matrices de coûts.",
        "modele": LLM_DEFAUT,
        "effort": EFFORT_DEFAUT,
        "env_modele": "MISTRAL_MODEL_BUDGET",
        "env_effort": "MISTRAL_REASONING_EFFORT_BUDGET",
        "branche": False,
    },
}

EFFORTS_MISTRAL = ("none", "low", "medium", "high")
EFFORTS_GLM = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
ALIAS_EFFORT = {"max": "xhigh"}

_DOTENV_CHARGE = False
_DOTENV_CHEMIN: Path | None = None


def charger_dotenv() -> Path | None:
    """Charge le premier `.env` trouvé (backend/, cwd, parents). Sans override."""
    global _DOTENV_CHARGE, _DOTENV_CHEMIN
    if _DOTENV_CHARGE:
        return _DOTENV_CHEMIN
    _DOTENV_CHARGE = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return None

    candidats: list[Path] = []
    for parent in Path(__file__).resolve().parents:
        candidats.append(parent / ".env")
    candidats.append(Path.cwd() / ".env")

    vus: set[Path] = set()
    for p in candidats:
        if not p.is_file():
            continue
        rp = p.resolve()
        if rp in vus:
            continue
        vus.add(rp)
        load_dotenv(rp, override=False)
        if _DOTENV_CHEMIN is None:
            _DOTENV_CHEMIN = rp
    load_dotenv(override=False)
    return _DOTENV_CHEMIN


def appliquer(*, model: str | None = None, effort: str | None = None,
              ocr_backend: str | None = None, ocr_model: str | None = None) -> None:
    """Pousse les choix CLI dans l'environnement (lu ensuite par les getters)."""
    if model:
        os.environ["MISTRAL_MODEL"] = model
    if effort:
        os.environ["MISTRAL_REASONING_EFFORT"] = effort
    if ocr_backend:
        os.environ["OCR_BACKEND"] = ocr_backend
    if ocr_model:
        os.environ["MISTRAL_OCR_MODEL"] = ocr_model


def api_key() -> str | None:
    """OCR + LLM : MISTRAL_API_KEY en priorité."""
    charger_dotenv()
    return os.environ.get("MISTRAL_API_KEY_BEN")


def api_key_source() -> str | None:
    charger_dotenv()
    return "MISTRAL_API_KEY_BEN"


def api_key_llm(model: str | None = None) -> tuple[str | None, str | None]:
    """Même clé que l'OCR (BEN en priorité)."""
    del model
    return api_key(), api_key_source()


def actif() -> bool:
    return bool(api_key())


def resoudre_modele(choix: str) -> str:
    cle = choix.strip()
    alias = ALIAS_LLM.get(cle.lower())
    if alias:
        return alias
    return NORMALISATION_LLM.get(cle, NORMALISATION_LLM.get(cle.lower(), cle))


def _lire(nom: str | None) -> str | None:
    if not nom:
        return None
    charger_dotenv()
    v = os.environ.get(nom)
    return v.strip() if v and v.strip() else None


def modele_llm() -> str:
    """Modèle LLM global (`MISTRAL_MODEL` / `--model`). Fallback des rôles sans surcharge."""
    return resoudre_modele(_lire("MISTRAL_MODEL") or LLM_DEFAUT)


def modele_pour(role: str) -> str:
    spec = ROLES[role]
    dedie = _lire(spec.get("env_modele"))
    if spec.get("type") == "ocr":
        return dedie or spec["modele"]
    if dedie:
        return resoudre_modele(dedie)
    if _lire("MISTRAL_MODEL"):
        return modele_llm()
    return resoudre_modele(spec["modele"])


def effort_llm() -> str:
    return _lire("MISTRAL_REASONING_EFFORT") or EFFORT_DEFAUT


def effort_pour(role: str) -> str:
    spec = ROLES[role]
    dedie = _lire(spec.get("env_effort"))
    if dedie:
        return dedie
    if spec.get("herite_effort_global", True) and _lire("MISTRAL_REASONING_EFFORT"):
        return effort_llm()
    return spec.get("effort") or EFFORT_DEFAUT


def max_tokens_pour(role: str) -> int:
    spec = ROLES[role]
    if "max_tokens" in spec:
        return int(spec["max_tokens"])
    charger_dotenv()
    return int(os.environ.get("MISTRAL_MAX_TOKENS", "64000"))


def budget_carte() -> int:
    charger_dotenv()
    brut = os.environ.get("CARTE_BUDGET_TOKENS")
    if brut:
        return int(brut)
    return int(ROLES["carte"].get("budget_tokens") or 100_000)


def modele_repli() -> str:
    return resoudre_modele(_lire("MISTRAL_MODEL_REPLI") or LLM_REPLI)


def famille_llm(model: str | None = None) -> str:
    m = (model or modele_llm()).lower()
    if "glm" in m or "zai" in m:
        return "glm"
    if "small" in m:
        return "small"
    if "medium" in m:
        return "medium"
    if "large" in m:
        return "large"
    return "autre"


def efforts_pour(model: str | None = None) -> tuple[str, ...]:
    return EFFORTS_GLM if famille_llm(model) == "glm" else EFFORTS_MISTRAL


def effort_api(effort: str) -> str:
    return ALIAS_EFFORT.get(effort, effort)


def effort_routeur() -> str:
    return effort_pour("routeur")


def modele_ocr() -> str:
    return modele_pour("ocr")


def ocr_backend() -> str:
    charger_dotenv()
    choisi = os.environ.get("OCR_BACKEND")
    if choisi:
        return choisi
    return "hybride" if actif() else "pdftotext"


def cache_dir() -> Path:
    charger_dotenv()
    return Path(os.environ.get("INGEST_CACHE", "/tmp/ingest_cache"))


def max_tokens() -> int:
    charger_dotenv()
    return int(os.environ.get("MISTRAL_MAX_TOKENS", "64000"))


def _role_snapshot(role: str) -> dict:
    spec = ROLES[role]
    out = {
        "usage": spec.get("usage"),
        "branche": spec.get("branche", True),
        "modele": modele_pour(role) if actif() or spec.get("type") == "ocr" else None,
    }
    if spec.get("type") != "ocr":
        out["effort"] = effort_api(effort_pour(role)) if actif() else None
        out["env_modele"] = spec.get("env_modele")
        out["env_effort"] = spec.get("env_effort")
    return out


def snapshot() -> dict:
    return {
        "ocr_backend": ocr_backend(),
        "ocr_model": modele_ocr() if ocr_backend() != "pdftotext" else None,
        "llm_actif": actif(),
        "llm_model": modele_llm() if actif() else None,
        "reasoning_effort": effort_api(effort_llm()) if actif() else None,
        "reasoning_effort_routeur": effort_api(effort_routeur()) if actif() else None,
        "roles": {r: _role_snapshot(r) for r in ROLES},
        "cle": api_key_source(),
        "cle_llm": api_key_llm()[1],
        "dotenv": str(_DOTENV_CHEMIN) if _DOTENV_CHEMIN else None,
        "cache": str(cache_dir()),
    }


def _masquer(cle: str) -> str:
    if len(cle) <= 10:
        return "…"
    return f"{cle[:6]}…{cle[-4:]}"


def banniere() -> str:
    cle, src = api_key(), api_key_source()
    cle_llm, src_llm = api_key_llm()
    ocr = ocr_backend()
    lignes = [
        "ingest_erc passe 1",
        f"  dotenv  : {_DOTENV_CHEMIN or '(aucun .env trouvé)'}",
        f"  clé OCR : {src + ' (' + _masquer(cle) + ')' if cle and src else 'ABSENTE'}",
        f"  clé LLM : {src_llm + ' (' + _masquer(cle_llm) + ')' if cle_llm and src_llm else 'ABSENTE — mode déterministe'}",
        f"  OCR     : {ocr}"
        + (f"  · {modele_ocr()} (OCR 4, hybride)" if ocr in ("mistral", "hybride", "auto") else "  · pdftotext local"),
        f"  LLM     : {modele_llm() if actif() else 'désactivé'}"
        + (f"  · effort global={effort_api(effort_llm())}" if actif() else ""),
    ]
    if actif():
        for role, spec in ROLES.items():
            if spec.get("type") == "ocr":
                continue
            marque = "" if spec.get("branche", True) else " (non branché)"
            lignes.append(
                f"    {role:<12} {modele_pour(role)}  · {effort_api(effort_pour(role))}{marque}"
            )
    lignes.append(f"  cache   : {cache_dir()}")
    return "\n".join(lignes)
