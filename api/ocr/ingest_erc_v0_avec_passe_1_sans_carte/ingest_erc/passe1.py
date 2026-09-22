"""Orchestrateur de la passe 1 : étapes 0 → 2, sortie = référentiel à valider par le BE."""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import config


def executer(chemins: list[str],
             on_etape: Callable[[str, str], None] | None = None) -> dict:
    from .excel_profil import profiler_excel
    from .extract_referentiel import faits_arrete, faits_plan, faits_tableaux_md, faits_tableur
    from .inventaire import inventorier
    from .reconcile_ref import reconcilier
    from .sig_profil import profiler_sig

    def etape(cle: str, msg: str) -> None:
        if on_etape:
            on_etape(cle, msg)

    config.charger_dotenv()
    t = {}
    etape("inventaire", "Lecture et OCR des documents…")
    t0 = time.time()
    docs = inventorier(chemins)
    t["0_inventaire"] = round(time.time() - t0, 2)

    etape("sig", "Profil des couches SIG…")
    t0 = time.time()
    couches, zones = [], []
    for d in docs:
        if d.role == "sig":
            c, z, _ = profiler_sig(d.chemin)
            couches += c
            zones += z
    t["1a_sig"] = round(time.time() - t0, 2)

    etape("referentiel", "Extraction du référentiel (tableur, plan, arrêté)…")
    t0 = time.time()
    faits, selection = [], {}
    for d in docs:
        if d.role == "tableur":
            faits += faits_tableur(d.nom, profiler_excel(d.chemin))
        elif d.role == "plan_gestion":
            f, sel = faits_plan(d)
            faits += f
            if any(p.markdown for p in d.pages):
                faits += faits_tableaux_md(d, sel["familles"])
            for a in d.sous_documents:          # décisions embarquées non déposées à part
                if a.get("role_probable") == "arrete" and not a.get("doublon_de") and "page_debut" in a:
                    sous = [p for p in d.pages if a["page_debut"] <= p.num <= a["page_fin"]]
                    if sum(len(p.texte) for p in sous) > 500:
                        faits += faits_arrete(d, pages=sous, nom=f"{d.nom} (annexe {a['num']})",
                                              cle=f"annexe{a['num']}")
            selection[d.nom] = {k: v for k, v in sel.items() if k != "retenues"} | {
                "retenues": [{"titre": s.titre, "pages": f"{s.page_debut}-{s.page_fin}",
                              "car": len(s.extrait)} for s in sel["retenues"]]}
        elif d.role == "arrete":
            faits += faits_arrete(d)
    t["1b_referentiel"] = round(time.time() - t0, 2)

    etape("reconciliation", "Réconciliation des sources…")
    t0 = time.time()
    ref = reconcilier(faits, zones, couches, docs)
    t["2_reconciliation"] = round(time.time() - t0, 2)
    etape("termine", "Référentiel prêt à valider.")

    return {
        "config": config.snapshot(),
        "documents": [{"nom": d.nom, "role": d.role, "indice": d.role_indice,
                       "pages": len(d.pages), "pages_scannees": len(d.pages_image),
                       "sous_documents": d.sous_documents,
                       "fiabilite_ocr": {str(p.num): p.fiabilite_ocr for p in d.pages
                                         if p.fiabilite_ocr is not None}} for d in docs],
        "sig": {"couches": [c.model_dump() for c in couches], "zones": [z.model_dump() for z in zones]},
        "selection_pdf": selection,
        "faits": [f.model_dump() for f in faits],
        "referentiel": ref.model_dump(),
        "durees_s": t,
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Passe 1 ingest_erc : inventaire → référentiel à valider.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Modèles LLM : small, medium, large, glm  (ou un id API exact).\n"
            "Raisonnement : --effort / --reasoning\n"
            "  · small/medium/large : none, low, medium, high\n"
            "  · glm (API Mistral)  : none, minimal, low, medium, high, xhigh\n"
            "OCR : hybride (défaut si clé : markdown Mistral + pdftotext),\n"
            "      mistral, pdftotext, auto.\n"
            "\n"
            "Exemples :\n"
            "  python -m ingest_erc.passe1 --check\n"
            "  python -m ingest_erc.passe1 sortie.json dossier/*\n"
            "  python -m ingest_erc.passe1 --model glm --effort high sortie.json dossier/*\n"
            "  python -m ingest_erc.passe1 --model medium --effort high sortie.json dossier/*"
        ),
    )
    p.add_argument("sortie", nargs="?", help="JSON de sortie (référentiel + faits).")
    p.add_argument("chemins", nargs="*", help="Fichiers du dossier ERC.")
    p.add_argument(
        "--model", default=None,
        help="Alias (small|medium|large|glm) ou id API. Défaut: glm (zai-glm-5-2).",
    )
    p.add_argument(
        "--effort", "--reasoning", dest="effort", default=None,
        help="Mode de raisonnement. Défaut: high. Alias: --reasoning.",
    )
    p.add_argument(
        "--ocr-backend", choices=("hybride", "mistral", "pdftotext", "auto"), default=None,
        help="hybride = markdown Mistral + couche native ; auto = pdftotext puis OCR si besoin.",
    )
    p.add_argument(
        "--ocr-model", default=None,
        help="Id OCR Mistral. Défaut: mistral-ocr-latest (OCR 4). "
             "Épingler avec mistral-ocr-4-0.",
    )
    p.add_argument(
        "--check", action="store_true",
        help="Affiche OCR / LLM / clé et quitte (aucun appel API, aucun fichier).",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    config.charger_dotenv()
    config.appliquer(
        model=args.model,
        effort=args.effort,
        ocr_backend=args.ocr_backend,
        ocr_model=args.ocr_model,
    )
    print(config.banniere(), flush=True)

    if args.check:
        return

    if not args.sortie or not args.chemins:
        _parser().error("sortie.json et au moins un fichier sont requis "
                        "(ou --check pour vérifier la config).")

    if config.ocr_backend() in ("mistral", "hybride") and not config.api_key():
        sys.exit("❌ OCR mistral/hybride demandé mais aucune clé "
                 "(MISTRAL_API_KEY ou MISTRAL_API_KEY_BEN).")

    out = executer(args.chemins)
    Path(args.sortie).write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str)
    )
    r = out["referentiel"]
    print(json.dumps(r["stats"], ensure_ascii=False), out["durees_s"])


if __name__ == "__main__":
    main()
