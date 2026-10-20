"""Configuration du pipeline (variables d'environnement + valeurs par défaut).

Tous les réglages qui changent le comportement (modèles, seuils, nombre
d'échantillons, politiques de déroulement) sont ici et nulle part ailleurs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    # --- Modèles Mistral -------------------------------------------------
    # Identifiants à vérifier dans la console Mistral (préversions publiques).
    api_key: str = field(default_factory=lambda: _env("MISTRAL_API_KEY", ""))
    ocr_model: str = field(default_factory=lambda: _env("PGCAL_OCR_MODEL", "mistral-ocr-4-1"))
    llm_model: str = field(default_factory=lambda: _env("PGCAL_LLM_MODEL", "mistral-large-4-0"))
    vision_model: str = field(default_factory=lambda: _env("PGCAL_VISION_MODEL", "mistral-large-4-0"))

    # --- Ingestion -------------------------------------------------------
    ocr_min_chars: int = 80            # en dessous : page considérée comme scannée -> OCR
    render_dpi: int = 130              # rendu des pages envoyées au modèle vision

    # --- Extraction LLM --------------------------------------------------
    samples_k: int = 3                 # échantillons indépendants par action (auto-cohérence)
    temperature: float = 0.3
    full_context_max_tokens: int = 300_000   # au-delà, on ne passe que les pages utiles
    use_critic: bool = True            # passe de relecture sur les actions à problèmes
    grid_vision_crosscheck: bool = False  # vision des grilles coupée : trop lente (1 à 4 min/page)
    page_classify_batch: int = 15

    # --- Confiance -------------------------------------------------------
    auto_threshold: float = 0.80       # >= : intégré directement au calendrier
    review_threshold: float = 0.55     # >= : proposé « à valider » ; sinon « à revoir »
    citation_min_score: int = 85       # score rapidfuzz pour considérer une citation retrouvée

    # --- Déroulement -----------------------------------------------------
    step_policy: str = "max"           # "tous les 2 à 3 ans" -> "min" (2), "max" (3), "mid"
    n_year_override: int | None = None # année réelle de N si différente de celle du document

    # --- Divers ----------------------------------------------------------
    runs_dir: Path = field(default_factory=lambda: Path(_env("PGCAL_RUNS_DIR", "runs")))
    offline: bool = False              # aucune requête API (étapes 1-3, 5-7 seulement)

    def public_dict(self) -> dict:
        d = asdict(self)
        d.pop("api_key", None)
        d["runs_dir"] = str(self.runs_dir)
        return d
