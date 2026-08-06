"""
multidocs — chaîne d'analyse multi-documents d'un dossier de compensation.

Étages :
  1. etape1_normalisation  fichier hétérogène → blocs ancrés
  2. etape2_triage         LLM : rôles + segments + paramètres du dossier
  3. etape3_planification  déterministe : carte + registre → jobs
  4. etape4_extraction     claims typés, ancrés, jamais réconciliés
  → suite (à venir) : réconciliation, semoir, patch, audit de couverture

Lancer (CLI) : python3 -m api.ocr.multidocs.run dossier_BE/ --out analyse_out/
API         : POST /api/projets/{id}/analyse-multidocs  (router.py + service.py)
"""

from .utils import (  # noqa: F401
    Claim,
    Kind,
    Bloc,
    Corpus,
    DocumentNormalise,
    Famille,
    RoleDoc,
    REGISTRE,
    Extracteur,
    extracteur,
)
