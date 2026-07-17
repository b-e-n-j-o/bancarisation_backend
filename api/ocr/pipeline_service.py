"""Pipeline extraction + ingestion — appelable depuis l'API."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

from .calcul_occurrences import annee_fin_suggeree, generer
from .extractions.extract_actions_mistral import extraire as extraire_actions
from .extractions.extract_dossier_mistral import extraire as extraire_dossier
from .extractions.extract_echeances_mistral import extraire as extraire_echeances
from .db.ingestion import connect, ingérer
from .lier_echeances_actions import lier
from .mistral_client import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    PRIX,
    PRIX_DEFAUT,
    Compteur,
)
from .models import ActionsResult, DossierResult, ExtractionResult

log = logging.getLogger("pipeline.service")

ProgressFn = Callable[[str, str | None], None]


def _horizon(dossier: DossierResult | None, echeances: ExtractionResult) -> int:
    if dossier and dossier.dossier.horizon.annee_fin:
        return dossier.dossier.horizon.annee_fin
    return annee_fin_suggeree(echeances.echeances)


def executer(
    projet_id: str,
    markdown: str,
    work_dir: Path,
    *,
    fichier_nom: str,
    replace: bool = True,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Extraction LLM → liaison → occurrences → ingestion en base."""

    def _progress(etape: str, detail: str | None = None) -> None:
        log.info("[%s] %s %s", projet_id, etape, detail or "")
        if on_progress:
            on_progress(etape, detail)

    work_dir.mkdir(parents=True, exist_ok=True)
    defaut = PRIX.get(model, PRIX_DEFAUT)
    prix_in, prix_out = defaut[0], defaut[1]
    cout_total = 0.0
    recap_etapes: list[dict] = []

    _progress("dossier", "Extraction métadonnées")
    c_dossier = Compteur(model, prix_in, prix_out)
    dossier_result = extraire_dossier(markdown, model, effort, c_dossier, max_tokens, False)
    dossier_path = work_dir / "dossier.json"
    dossier_path.write_text(dossier_result.model_dump_json(indent=2), encoding="utf-8")
    cout_total += c_dossier.cout
    recap_etapes.append({"etape": "dossier", "cout_usd": round(c_dossier.cout, 4)})

    _progress("actions", "Extraction fiches-actions")
    c_actions = Compteur(model, prix_in, prix_out)
    actions_result = extraire_actions(markdown, model, effort, c_actions, max_tokens, False)
    actions_path = work_dir / "actions.json"
    actions_path.write_text(actions_result.model_dump_json(indent=2), encoding="utf-8")
    cout_total += c_actions.cout
    recap_etapes.append({"etape": "actions", "cout_usd": round(c_actions.cout, 4)})

    _progress("echeances", "Extraction échéances")
    c_ech = Compteur(model, prix_in, prix_out)
    echeances_result = extraire_echeances(markdown, model, effort, c_ech, max_tokens, False)
    echeances_path = work_dir / "echeances_mistral.json"
    echeances_path.write_text(echeances_result.model_dump_json(indent=2), encoding="utf-8")
    cout_total += c_ech.cout
    recap_etapes.append({"etape": "echeances", "cout_usd": round(c_ech.cout, 4)})

    _progress("liaison", "Liaison échéances ↔ actions")
    liees = lier(echeances_result, actions_result)
    liees_path = work_dir / "echeances_liees.json"
    liees_path.write_text(liees.model_dump_json(indent=2), encoding="utf-8")

    _progress("occurrences", "Génération calendrier")
    annee_fin = _horizon(dossier_result, echeances_result)
    occs, non_placables = generer(liees.echeances, annee_fin=annee_fin)
    occ_path = work_dir / "occurrences.json"
    occ_path.write_text(
        json.dumps(
            {
                "occurrences": [o.model_dump(mode="json") for o in occs],
                "nb_non_placables": len(non_placables),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _progress("ingestion", "Persistance en base")
    fichier_hash = hashlib.sha256(markdown.encode()).hexdigest()
    with connect() as conn:
        recap_ingestion = ingérer(
            conn,
            projet_id,
            dossier=dossier_result,
            actions=actions_result,
            echeances_liees=liees,
            occurrences=occs,
            fichier_nom=fichier_nom,
            fichier_hash=fichier_hash,
            modele_ocr="mistral-ocr-latest",
            modele_llm=model,
            nb_non_placables=len(non_placables),
            replace=replace,
        )

    return {
        "projet_id": projet_id,
        "cout_total_usd": round(cout_total, 4),
        "etapes": recap_etapes,
        "ingestion": recap_ingestion,
        "stats": {
            "actions": len(actions_result.actions),
            "echeances": len(echeances_result.echeances),
            "liaisons": len(liees.liaisons),
            "occurrences": len(occs),
            "non_placables": len(non_placables),
            "horizon": annee_fin,
        },
        "work_dir": str(work_dir),
    }
