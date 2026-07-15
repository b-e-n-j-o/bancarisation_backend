#!/usr/bin/env python3
"""
pipeline_extraction.py — Orchestrateur bout-en-bout : extraction LLM → base.

    full.md
      ├─► dossier + actions + échéances (3 appels Mistral)
      ├─► liaison + occurrences (déterministe)
      └─► ingestion_base_de_donnees (Supabase)

Usage :
    python pipeline_extraction.py --creer-projet
    python pipeline_extraction.py --projet <uuid>
    python pipeline_extraction.py --projet <uuid> --skip-llm
    python pipeline_extraction.py --skip-ingestion   # JSON seulement
    python pipeline_extraction.py --projet <uuid> --replace

Logs :
    logs/pipeline_YYYYMMDD_HHMMSS.log
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .calcul_occurrences import annee_fin_suggeree, generer
from .extractions.extract_actions_mistral import extraire as extraire_actions
from .extractions.extract_dossier_mistral import extraire as extraire_dossier
from .extractions.extract_echeances_mistral import extraire as extraire_echeances
from .ingestion_base_de_donnees import connect, ingérer, resoudre_projet_id
from .lier_echeances_actions import lier
from .mistral_client import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    PRIX,
    PRIX_DEFAUT,
    Compteur,
    configurer_logging,
)
from .models import ActionsResult, DossierResult, ExtractionResult

_SCRIPT_DIR = Path(__file__).resolve().parent

load_dotenv(_SCRIPT_DIR / ".env")

log = logging.getLogger("pipeline")

DEFAULT_MD = _SCRIPT_DIR / "ocr_output" / "full.md"
LOG_DIR = _SCRIPT_DIR / "logs"


def _horizon(dossier: DossierResult | None, echeances: ExtractionResult) -> int:
    if dossier and dossier.dossier.horizon.annee_fin:
        return dossier.dossier.horizon.annee_fin
    return annee_fin_suggeree(echeances.echeances)


def main() -> None:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = configurer_logging(LOG_DIR, run_id)
    log.info("=== Pipeline extraction démarré ===")
    log.info("Log fichier : %s", log_file)
    print(f"📝 Log → {log_file}")

    p = argparse.ArgumentParser(description="Pipeline complet dossier + actions + échéances.")
    p.add_argument("markdown", nargs="?", default=str(DEFAULT_MD))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default=DEFAULT_EFFORT,
                   choices=["none", "low", "medium", "high"])
    p.add_argument("--schema", action="store_true")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--limite-chars", type=int, default=None)
    p.add_argument("--prix-in", type=float, default=None)
    p.add_argument("--prix-out", type=float, default=None)
    p.add_argument("--projet", default=None,
                   help="UUID projet existant — active l'ingestion en base")
    p.add_argument("--creer-projet", action="store_true",
                   help="Crée un projet depuis dossier.json puis ingère en base")
    p.add_argument("--nom-projet", default=None,
                   help="Nom du projet si --creer-projet (sinon nom_operation du dossier)")
    p.add_argument("--skip-echeances", action="store_true")
    p.add_argument("--skip-llm", action="store_true",
                   help="Pas d'appel LLM : utilise dossier/actions/echeances.json existants")
    p.add_argument("--skip-ingestion", action="store_true",
                   help="N'écrit pas en base (JSON locaux seulement)")
    p.add_argument("--replace", action="store_true",
                   help="Re-semis : supprime les occurrences IA non modifiées par l'utilisateur")
    args = p.parse_args()

    src = Path(args.markdown)
    if not src.is_absolute():
        if not src.exists():
            candidat = _SCRIPT_DIR / args.markdown
            src = candidat if candidat.exists() else src
    if not src.exists():
        sys.exit(f"❌ Markdown introuvable : {src}")

    markdown = src.read_text(encoding="utf-8")
    if args.limite_chars:
        markdown = markdown[:args.limite_chars]
        log.info("Markdown tronqué à %d caractères", args.limite_chars)

    defaut = PRIX.get(args.model, PRIX_DEFAUT)
    prix_in = args.prix_in if args.prix_in is not None else defaut[0]
    prix_out = args.prix_out if args.prix_out is not None else defaut[1]

    dossier_path = _SCRIPT_DIR / "dossier.json"
    actions_path = _SCRIPT_DIR / "actions.json"
    echeances_path = _SCRIPT_DIR / "echeances_mistral.json"
    liees_path = _SCRIPT_DIR / "echeances_liees.json"
    occ_path = _SCRIPT_DIR / "occurrences.json"

    dossier_result: DossierResult | None = None
    actions_result: ActionsResult | None = None
    echeances_result: ExtractionResult | None = None
    cout_total = 0.0
    recap_etapes: list[dict] = []

    if not args.skip_llm:
        # --- Étape A : dossier ------------------------------------------------
        print("\n" + "═" * 62)
        print("  ÉTAPE A — Métadonnées dossier")
        print("═" * 62)
        log.info("Étape A — dossier")
        c_dossier = Compteur(args.model, prix_in, prix_out)
        dossier_result = extraire_dossier(
            markdown, args.model, args.effort, c_dossier, args.max_tokens, args.schema,
        )
        dossier_path.write_text(dossier_result.model_dump_json(indent=2), encoding="utf-8")
        h = dossier_result.dossier.horizon
        print(f"✅ dossier.json — horizon {h.annee_debut}→{h.annee_fin} ({h.duree_ans} ans)")
        print(c_dossier.rapport())
        cout_total += c_dossier.cout
        recap_etapes.append({"etape": "dossier", "cout_usd": round(c_dossier.cout, 4)})

        # --- Étape B : actions ------------------------------------------------
        print("\n" + "═" * 62)
        print("  ÉTAPE B — Fiches-actions (contenu intégral)")
        print("═" * 62)
        log.info("Étape B — actions")
        c_actions = Compteur(args.model, prix_in, prix_out)
        actions_result = extraire_actions(
            markdown, args.model, args.effort, c_actions, args.max_tokens, args.schema,
        )
        actions_path.write_text(actions_result.model_dump_json(indent=2), encoding="utf-8")
        print(f"✅ actions.json — {len(actions_result.actions)} fiche(s)")
        print(c_actions.rapport())
        cout_total += c_actions.cout
        recap_etapes.append({"etape": "actions", "cout_usd": round(c_actions.cout, 4)})

        # --- Étape C : échéances (full.md entier) -----------------------------
        if not args.skip_echeances:
            print("\n" + "═" * 62)
            print("  ÉTAPE C — Échéances (full.md entier)")
            print("═" * 62)
            log.info("Étape C — échéances (document complet)")
            c_ech = Compteur(args.model, prix_in, prix_out)
            echeances_result = extraire_echeances(
                markdown, args.model, args.effort, c_ech, args.max_tokens, args.schema,
            )
            echeances_path.write_text(
                echeances_result.model_dump_json(indent=2), encoding="utf-8",
            )
            print(f"✅ echeances_mistral.json — {len(echeances_result.echeances)} échéance(s)")
            print(c_ech.rapport())
            cout_total += c_ech.cout
            recap_etapes.append({"etape": "echeances", "cout_usd": round(c_ech.cout, 4)})

    # --- Chargement si skip-llm ou skip partiel --------------------------------
    if dossier_result is None and dossier_path.exists():
        dossier_result = DossierResult.model_validate_json(
            dossier_path.read_text(encoding="utf-8")
        )
        log.info("Dossier chargé depuis %s", dossier_path)
    if actions_result is None and actions_path.exists():
        actions_result = ActionsResult.model_validate_json(
            actions_path.read_text(encoding="utf-8")
        )
        log.info("Actions chargées depuis %s", actions_path)
    if echeances_result is None and echeances_path.exists():
        echeances_result = ExtractionResult.model_validate_json(
            echeances_path.read_text(encoding="utf-8")
        )
        log.info("Échéances chargées depuis %s", echeances_path)

    if not echeances_result:
        sys.exit("❌ Pas d'échéances (lance sans --skip-echeances ou fournis echeances_mistral.json)")
    if not actions_result:
        sys.exit("❌ Pas d'actions (lance sans --skip-llm ou fournis actions.json)")

    # --- Étape D : liaison -----------------------------------------------------
    print("\n" + "═" * 62)
    print("  ÉTAPE D — Liaison échéances ↔ actions (déterministe)")
    print("═" * 62)
    log.info("Étape D — liaison")
    liees = lier(echeances_result, actions_result)
    liees_path.write_text(liees.model_dump_json(indent=2), encoding="utf-8")
    print(f"✅ echeances_liees.json — {len(liees.liaisons)} lien(s), {len(liees.orphelines)} orpheline(s)")

    # --- Étape E : occurrences -------------------------------------------------
    print("\n" + "═" * 62)
    print("  ÉTAPE E — Génération occurrences")
    print("═" * 62)
    annee_fin = _horizon(dossier_result, echeances_result)
    log.info("Horizon occurrences : %d", annee_fin)
    occs, non_placables = generer(echeances_result.echeances, annee_fin=annee_fin)
    occ_path.write_text(json.dumps({
        "annee_fin": annee_fin,
        "annee_fin_source": "dossier" if dossier_result and dossier_result.dossier.horizon.annee_fin else "inferee",
        "nb_occurrences": len(occs),
        "nb_non_placables": len(non_placables),
        "occurrences": [o.model_dump(mode="json") for o in occs],
        "non_placables": [e.model_dump(mode="json") for e in non_placables],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ occurrences.json — {len(occs)} occurrence(s), horizon {annee_fin}")

    # --- Étape F : ingestion base ----------------------------------------------
    recap_ingestion: dict | None = None
    projet_id_final: str | None = args.projet
    if (args.projet or args.creer_projet) and not args.skip_ingestion:
        print("\n" + "═" * 62)
        print("  ÉTAPE F — Ingestion base (projet_id)")
        print("═" * 62)
        fichier_hash = hashlib.sha256(markdown.encode()).hexdigest()
        with connect() as conn:
            projet_id_final = resoudre_projet_id(
                conn,
                projet_id=args.projet,
                creer=args.creer_projet,
                dossier=dossier_result,
                nom=args.nom_projet,
            )
            if args.creer_projet:
                print(f"🆕 Projet créé : {projet_id_final}")
            log.info("Étape F — ingestion projet %s", projet_id_final)
            recap_ingestion = ingérer(
                conn,
                projet_id_final,
                dossier=dossier_result,
                actions=actions_result,
                echeances_liees=liees,
                occurrences=occs,
                fichier_nom=src.name,
                fichier_hash=fichier_hash,
                modele_ocr="mistral-ocr-latest",
                modele_llm=args.model if not args.skip_llm else None,
                nb_non_placables=len(non_placables),
                replace=args.replace,
            )
        print(f"✅ Base — {recap_ingestion['occurrences_inserees']} occurrence(s) insérée(s)")
        for k, v in recap_ingestion.items():
            print(f"   {k:<28} {v}")
    elif not args.skip_ingestion and not args.projet and not args.creer_projet:
        print("\nℹ️  Pas d'ingestion (--projet ou --creer-projet absent). JSON locaux uniquement.")

    # --- Récap ---------------------------------------------------------------
    recap = {
        "run_id": run_id,
        "log": str(log_file),
        "markdown": str(src),
        "model": args.model,
        "effort": args.effort,
        "cout_total_usd": round(cout_total, 4),
        "etapes": recap_etapes,
        "outputs": {
            "dossier": str(dossier_path),
            "actions": str(actions_path),
            "echeances": str(echeances_path),
            "echeances_liees": str(liees_path),
            "occurrences": str(occ_path),
        },
        "projet_id": projet_id_final,
        "ingestion": recap_ingestion,
        "stats": {
            "actions": len(actions_result.actions),
            "echeances": len(echeances_result.echeances),
            "liaisons": len(liees.liaisons),
            "occurrences": len(occs),
            "horizon": annee_fin,
        },
    }
    recap_path = _SCRIPT_DIR / "pipeline_recap.json"
    recap_path.write_text(json.dumps(recap, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "═" * 62)
    print(f"  PIPELINE TERMINÉ — coût LLM total ≈ ${cout_total:.4f}")
    print("═" * 62)
    print(f"   Récap machine → {recap_path}")
    log.info("Pipeline terminé — coût total $%.4f", cout_total)


if __name__ == "__main__":
    main()
