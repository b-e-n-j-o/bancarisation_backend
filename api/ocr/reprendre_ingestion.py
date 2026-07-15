#!/usr/bin/env python3
"""Reprise d'ingestion depuis un work_dir (sans relancer OCR/LLM)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .calcul_occurrences import annee_fin_suggeree, generer
from .ingestion_base_de_donnees import charger_occurrences, connect, ingérer
from .models import ActionsResult, DossierResult, EcheancesLieesResult, ExtractionResult

_SCRIPT_DIR = Path(__file__).resolve().parent


def _horizon(dossier: DossierResult | None, echeances: ExtractionResult) -> int:
    if dossier and dossier.dossier.horizon.annee_fin:
        return dossier.dossier.horizon.annee_fin
    return annee_fin_suggeree(echeances.echeances)


def _assurer_occurrences(wd: Path) -> Path:
    occ_path = wd / "occurrences.json"
    if occ_path.exists():
        return occ_path

    dossier_path = wd / "dossier.json"
    echeances_path = wd / "echeances_mistral.json"
    if not echeances_path.exists():
        raise SystemExit(f"❌ {echeances_path} introuvable — impossible de régénérer les occurrences.")

    dossier_result = None
    if dossier_path.exists():
        dossier_result = DossierResult.model_validate_json(
            dossier_path.read_text(encoding="utf-8")
        )
    echeances_result = ExtractionResult.model_validate_json(
        echeances_path.read_text(encoding="utf-8")
    )
    annee_fin = _horizon(dossier_result, echeances_result)
    occs, non_placables = generer(echeances_result.echeances, annee_fin=annee_fin)
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
    print(f"📅 occurrences.json régénéré ({len(occs)} occurrences)")
    return occ_path


def main() -> None:
    p = argparse.ArgumentParser(description="Ingère un work_dir sans relancer Mistral.")
    p.add_argument("projet_id", help="UUID bancarisation.projets.id")
    p.add_argument(
        "--work-dir",
        default=None,
        help="Dossier work (défaut : work/<projet_id>)",
    )
    p.add_argument("--replace", action="store_true", help="Remplace les données IA existantes")
    p.add_argument("--fichier-nom", default=None, help="Nom du PDF source")
    args = p.parse_args()

    wd = Path(args.work_dir) if args.work_dir else _SCRIPT_DIR / "work" / args.projet_id
    if not wd.is_dir():
        raise SystemExit(f"❌ work_dir introuvable : {wd}")

    dossier_path = wd / "dossier.json"
    actions_path = wd / "actions.json"
    liees_path = wd / "echeances_liees.json"
    for label, path in [
        ("dossier", dossier_path),
        ("actions", actions_path),
        ("echeances_liees", liees_path),
    ]:
        if not path.exists():
            raise SystemExit(f"❌ {label} introuvable : {path}")

    occ_path = _assurer_occurrences(wd)

    dossier_result = DossierResult.model_validate_json(
        dossier_path.read_text(encoding="utf-8")
    )
    actions_result = ActionsResult.model_validate_json(
        actions_path.read_text(encoding="utf-8")
    )
    echeances_liees = EcheancesLieesResult.model_validate_json(
        liees_path.read_text(encoding="utf-8")
    )
    occs, nb_non = charger_occurrences(occ_path)

    md_path = wd / "ocr_output" / "full.md"
    fichier_nom = args.fichier_nom or "source.pdf"
    fichier_hash = None
    if md_path.exists():
        fichier_hash = hashlib.sha256(md_path.read_text(encoding="utf-8").encode()).hexdigest()

    print(f"📦 Reprise ingestion — projet {args.projet_id}")
    print(f"   work_dir    : {wd}")
    print(f"   actions     : {len(actions_result.actions)}")
    print(f"   échéances   : {len(echeances_liees.echeances)}")
    print(f"   occurrences : {len(occs)}")

    with connect() as conn:
        recap = ingérer(
            conn,
            args.projet_id,
            dossier=dossier_result,
            actions=actions_result,
            echeances_liees=echeances_liees,
            occurrences=occs,
            fichier_nom=fichier_nom,
            fichier_hash=fichier_hash,
            modele_ocr="mistral-ocr-latest",
            modele_llm=None,
            nb_non_placables=nb_non,
            replace=args.replace,
        )

    print("\n✅ Ingestion terminée :")
    for k, v in recap.items():
        print(f"   {k:<28} {v}")


if __name__ == "__main__":
    main()
