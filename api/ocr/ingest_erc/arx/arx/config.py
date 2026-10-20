"""Configuration (variables d'environnement + valeurs par défaut)."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    # Modèles Mistral (identifiants à confirmer dans la console)
    api_key: str = field(default_factory=lambda: _env("MISTRAL_API_KEY", ""))
    ocr_model: str = field(default_factory=lambda: _env("ARX_OCR_MODEL", "mistral-ocr-4-1"))
    llm_model: str = field(default_factory=lambda: _env("ARX_LLM_MODEL", "mistral-large-4-0"))
    vision_model: str = field(default_factory=lambda: _env("ARX_VISION_MODEL", "mistral-large-4-0"))

    # Ingestion
    ocr_min_chars: int = 80              # page sous ce seuil -> OCR
    force_ocr: bool = False              # tout passer en OCR (PDF scannés « avec texte » de mauvaise qualité)

    # Extraction
    samples_k: int = 2                   # échantillons par article (accord -> confiance)
    temperature: float = 0.2
    contradiction_pass: bool = True      # passe LLM de recherche de contradictions internes
    figures_vision: bool = False         # lecture des légendes de figures par le modèle vision (désactivée)

    # Vérification / confiance
    citation_min_score: int = 85
    auto_threshold: float = 0.80         # >= : obligation retenue telle quelle
    review_threshold: float = 0.55       # >= : à valider ; sinon à revoir

    runs_dir: Path = field(default_factory=lambda: Path(_env("ARX_RUNS_DIR", "runs")))
    offline: bool = False

    def public_dict(self) -> dict:
        d = asdict(self)
        d.pop("api_key", None)
        d["runs_dir"] = str(self.runs_dir)
        return d
