"""
claims_vers_calendrier.py — Semoir + ingestion depuis claims.jsonl (0 LLM).

Usage :
    python -m api.ocr.claims_vers_calendrier <projet_id>
    python -m api.ocr.claims_vers_calendrier <projet_id> --replace
    python -m api.ocr.claims_vers_calendrier <projet_id> \\
        --claims work/<id>/multidocs/claims.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .calcul_occurrences import annee_fin_suggeree, generer
from .db.ingestion import connect, ingérer
from .lier_echeances_actions import lier
from .models import (
    ActionFiche,
    ActionsResult,
    DossierResult,
    Echeance,
    ExtractionResult,
)
from .multidocs.utils.claims import Kind

_OCR_DIR = Path(__file__).resolve().parent


def charger_claims_jsonl(chemin: Path) -> tuple[ActionsResult, ExtractionResult]:
    actions: list[ActionFiche] = []
    echeances: list[Echeance] = []
    for i, ligne in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            claim = json.loads(ligne)
        except json.JSONDecodeError as err:
            raise SystemExit(f"❌ JSONL invalide ligne {i} : {err}") from err
        kind = claim.get("kind")
        donnees = claim.get("donnees") or {}
        if kind == Kind.action.value:
            actions.append(ActionFiche.model_validate(donnees))
        elif kind == Kind.echeance_regle.value:
            echeances.append(Echeance.model_validate(donnees))
    if not echeances:
        raise ValueError(f"Aucune échéance (kind=echeance_regle) dans {chemin}")
    return ActionsResult(actions=actions), ExtractionResult(echeances=echeances)


def resoudre_claims_path(projet_id: str, claims: str | None) -> Path:
    if claims:
        p = Path(claims)
        if not p.is_absolute():
            candidat = _OCR_DIR / p
            p = candidat if candidat.exists() else p
        return p
    defaut = _OCR_DIR / "work" / projet_id / "multidocs" / "claims.jsonl"
    return defaut


def executer(
    projet_id: str,
    claims_path: Path,
    *,
    replace: bool = True,
    annee_fin: int | None = None,
    fichier_nom: str | None = None,
) -> dict:
    actions_result, echeances_result = charger_claims_jsonl(claims_path)
    liees = lier(echeances_result, actions_result)

    wd = claims_path.parent  # …/multidocs
    work_projet = wd.parent if wd.name == "multidocs" else wd

    dossier_result: DossierResult | None = None
    for candidat in (work_projet / "dossier.json", wd / "dossier.json"):
        if candidat.exists():
            dossier_result = DossierResult.model_validate_json(
                candidat.read_text(encoding="utf-8")
            )
            break

    horizon = annee_fin
    if horizon is None:
        if dossier_result and dossier_result.dossier.horizon.annee_fin:
            horizon = dossier_result.dossier.horizon.annee_fin
        else:
            horizon = annee_fin_suggeree(list(liees.echeances))

    occs, non_placables = generer(list(liees.echeances), annee_fin=horizon)

    # Artefacts rejouables à côté des claims
    (wd / "actions.json").write_text(
        actions_result.model_dump_json(indent=2), encoding="utf-8",
    )
    (wd / "echeances_liees.json").write_text(
        liees.model_dump_json(indent=2), encoding="utf-8",
    )
    (wd / "occurrences.json").write_text(
        json.dumps(
            {
                "occurrences": [o.model_dump(mode="json") for o in occs],
                "nb_non_placables": len(non_placables),
                "annee_fin": horizon,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    nom = fichier_nom or "claims.jsonl"
    fichier_hash = hashlib.sha256(claims_path.read_bytes()).hexdigest()

    print(f"📦 Claims → calendrier — projet {projet_id}")
    print(f"   claims      : {claims_path}")
    print(f"   actions     : {len(actions_result.actions)}")
    print(f"   échéances   : {len(liees.echeances)}")
    print(f"   horizon     : {horizon}")
    print(f"   occurrences : {len(occs)}  · non placables : {len(non_placables)}")
    if dossier_result is None:
        print("   metadata    : absentes (OK — non requises pour le semoir)")

    with connect() as conn:
        recap = ingérer(
            conn,
            projet_id,
            dossier=dossier_result,
            actions=actions_result,
            echeances_liees=liees,
            occurrences=occs,
            fichier_nom=nom,
            fichier_hash=fichier_hash,
            modele_ocr=None,
            modele_llm="claims-jsonl",
            nb_non_placables=len(non_placables),
            replace=replace,
        )

    print("\n✅ Ingestion terminée :")
    for k, v in recap.items():
        print(f"   {k:<28} {v}")
    return recap


def main() -> None:
    p = argparse.ArgumentParser(
        description="Génère les occurrences et ingère depuis claims.jsonl (sans LLM)."
    )
    p.add_argument("projet_id", help="UUID bancarisation.projets.id")
    p.add_argument("--claims", default=None, help="Chemin claims.jsonl")
    p.add_argument("--replace", action="store_true", default=True,
                   help="Remplace les occurrences IA non modifiées (défaut: oui)")
    p.add_argument("--no-replace", action="store_true",
                   help="Refuse si des occurrences IA existent déjà")
    p.add_argument("--annee-fin", type=int, default=None)
    p.add_argument("--fichier-nom", default=None)
    args = p.parse_args()

    claims_path = resoudre_claims_path(args.projet_id, args.claims)
    if not claims_path.is_file():
        raise SystemExit(f"❌ claims introuvable : {claims_path}")

    try:
        executer(
            args.projet_id,
            claims_path,
            replace=not args.no_replace,
            annee_fin=args.annee_fin,
            fichier_nom=args.fichier_nom,
        )
    except ValueError as err:
        raise SystemExit(f"❌ {err}") from err


if __name__ == "__main__":
    main()
