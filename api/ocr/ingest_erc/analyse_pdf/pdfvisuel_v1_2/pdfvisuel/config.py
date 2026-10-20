"""Modèles du module 2 (vision). Un seul endroit à changer pour un test A/B.

Les deux modèles passent par l'API Mistral (`api.mistral.ai`).

Vision : mistral-large-4-0 (bascule GLM : "zai-glm-5-3")
OCR    : mistral-ocr-4-1 (plus mistral-ocr-latest / 4.0)

`MISTRAL_VISION_MODEL` / `MISTRAL_OCR_MODEL` surchargent sans toucher au fichier.
"""

VISION_MODELE = "mistral-large-4-0"
# VISION_MODELE = "zai-glm-5-3"

OCR_MODELE = "mistral-ocr-4-1"

# USD par million de tokens (entrée, sortie).
PRIX_VISION_M = {
    "mistral-large-4-0": (1.36, 4.18),
}

# EUR par page OCR (une image envoyée à l'API = une page).
PRIX_OCR_EUR_PAR_PAGE = {
    "mistral-ocr-4-1": 3.4,
}
