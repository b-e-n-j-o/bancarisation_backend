"""Configuration OCR / LLM de la passe 1.

Charge le `.env` du backend (même clés que le smoke test Mistral) et expose
les choix de modèles. Rien n'est figé à l'import : on relit l'environnement
à chaque appel, pour que `--model` / `--effort` sur la CLI prennent effet.
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
}

# OCR 4 : mistral-ocr-latest pointe vers OCR 4 depuis le 23/06/2026.
# Épingler mistral-ocr-4-0 via MISTRAL_OCR_MODEL si on veut figer la version.
OCR_MODELE_DEFAUT = "mistral-ocr-latest"
LLM_DEFAUT = "glm"  # → zai-glm-5-2
EFFORT_DEFAUT = "high"
EFFORT_ROUTEUR_DEFAUT = "low"

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
    """OCR + LLM : MISTRAL_API_KEY_BEN en priorité (la clé prod n'est pas encore activée)."""
    charger_dotenv()
    return os.environ.get("MISTRAL_API_KEY_BEN") or os.environ.get("MISTRAL_API_KEY")


def api_key_source() -> str | None:
    charger_dotenv()
    if os.environ.get("MISTRAL_API_KEY_BEN"):
        return "MISTRAL_API_KEY_BEN"
    if os.environ.get("MISTRAL_API_KEY"):
        return "MISTRAL_API_KEY"
    return None


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


def modele_llm() -> str:
    charger_dotenv()
    return resoudre_modele(os.environ.get("MISTRAL_MODEL", LLM_DEFAUT))


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


def effort_llm() -> str:
    charger_dotenv()
    return os.environ.get("MISTRAL_REASONING_EFFORT", EFFORT_DEFAUT)


def effort_routeur() -> str:
    charger_dotenv()
    return os.environ.get("MISTRAL_REASONING_EFFORT_ROUTEUR", EFFORT_ROUTEUR_DEFAUT)


def modele_ocr() -> str:
    charger_dotenv()
    return os.environ.get("MISTRAL_OCR_MODEL", OCR_MODELE_DEFAUT)


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
    return int(os.environ.get("MISTRAL_MAX_TOKENS", "16000"))


def snapshot() -> dict:
    return {
        "ocr_backend": ocr_backend(),
        "ocr_model": modele_ocr() if ocr_backend() != "pdftotext" else None,
        "llm_actif": actif(),
        "llm_model": modele_llm() if actif() else None,
        "reasoning_effort": effort_api(effort_llm()) if actif() else None,
        "reasoning_effort_routeur": effort_api(effort_routeur()) if actif() else None,
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
        + (f"  · reasoning_effort={effort_api(effort_llm())}" if actif() else ""),
        f"  cache   : {cache_dir()}",
    ]
    return "\n".join(lignes)
