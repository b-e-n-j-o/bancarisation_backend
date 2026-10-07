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
             on_etape: Callable[[str, str], None] | None = None,
             cartes: dict | None = None,
             roles_forces: dict[str, str] | None = None) -> dict:
    from .arrete import charger_ou_lire_arrete, faits_depuis_fiche
    from .carte import Regles, charger_ou_cartographier
    from .carte_classeur import profiler_classeur
    from .excel_profil import profiler_excel
    from .extract_referentiel import faits_arrete, faits_plan, faits_tableaux_md, faits_tableur
    from .inventaire import inventorier
    from .reconcile_ref import reconcilier
    from .remarques import trier
    from .sig_profil import profiler_sig

    config.charger_dotenv()
    from . import llm
    llm.reset_compteur_tokens()
    t: dict[str, float] = {}
    pipeline_t0 = time.time()

    def chronometrer(cle: str, t0: float) -> None:
        dt = round(time.time() - t0, 2)
        t[cle] = dt
        print(f"⏱ {cle} : {dt}s", flush=True)

    def etape(cle: str, msg: str) -> None:
        if on_etape:
            on_etape(cle, msg)

    etape("inventaire", "Lecture et OCR des documents…")
    t0 = time.time()
    docs = inventorier(chemins, roles_forces)
    chronometrer("inventaire", t0)

    etape("carte", "Cartographie du plan de gestion et du tableur…")
    t0 = time.time()
    cartes = dict(cartes or {})
    for d in docs:
        if d.role == "plan_gestion" and d.nom not in cartes:
            try:
                c = charger_ou_cartographier(d, config.cache_dir())
            except Exception as err:  # noqa: BLE001
                print(f"   ⚠️  carte LLM ignorée ({d.nom}) : {err}", flush=True)
                c = None
            if c:
                cartes[d.nom] = c
    regles = Regles.depuis(next(iter(cartes.values()))) if cartes else Regles.defaut()
    cartes_classeur, vues_classeur, faits_classeur = {}, [], []
    for d in docs:
        if d.role != "tableur":
            continue
        try:
            carte_xl, _tabs, _cols, faits_xl, vue_xl = profiler_classeur(
                d, config.cache_dir(), regles)
        except Exception as err:  # noqa: BLE001
            print(f"   ⚠️  carte classeur ignorée ({d.nom}) : {err}", flush=True)
            continue
        cartes_classeur[d.nom] = carte_xl
        vues_classeur.append(vue_xl)
        faits_classeur += faits_xl
    chronometrer("carte", t0)

    etape("sig", "Profil des couches SIG…")
    t0 = time.time()
    couches, zones = [], []
    for d in docs:
        if d.role == "sig":
            try:
                c, z, _ = profiler_sig(d.chemin, regles)
            except Exception as err:  # noqa: BLE001
                print(f"   ⚠️  SIG ignoré ({d.nom}) : {err}", flush=True)
                continue
            couches += c
            zones += z
    chronometrer("sig", t0)

    def _faits_fiche_arrete(d, pages=None, cle="projet"):
        try:
            fa = charger_ou_lire_arrete(d, config.cache_dir(), pages)
        except Exception as err:  # noqa: BLE001
            print(f"   ⚠️  fiche arrêté ignorée ({d.nom}) : {err}", flush=True)
            return []
        return faits_depuis_fiche(fa, d, cle=cle) if fa else []

    etape("referentiel", "Extraction du référentiel (tableur, plan, arrêté)…")
    t0 = time.time()
    faits, selection = [], {}
    for d in docs:
        if d.role == "tableur":
            faits += faits_tableur(d.nom, profiler_excel(d.chemin, regles))
        elif d.role == "plan_gestion":
            carte = cartes.get(d.nom)
            f, sel = faits_plan(d, carte, regles)
            faits += f
            if any(p.markdown for p in d.pages):
                faits += faits_tableaux_md(d, sel["familles"], carte, regles)
            for a in d.sous_documents:          # décisions embarquées non déposées à part
                if a.get("role_probable") == "arrete" and not a.get("doublon_de") and "page_debut" in a:
                    sous = [p for p in d.pages if a["page_debut"] <= p.num <= a.get("page_fin", a["page_debut"])]
                    if sum(len(p.texte) for p in sous) > 500:
                        faits += faits_arrete(d, pages=sous, nom=f"{d.nom} (annexe {a['num']})",
                                              cle=f"annexe{a['num']}")
                    faits += _faits_fiche_arrete(
                        d, (a["page_debut"], a.get("page_fin", a["page_debut"])),
                        cle=f"annexe{a['num']}")
            selection[d.nom] = {k: v for k, v in sel.items() if k != "retenues"} | {
                "retenues": [{"titre": s.titre, "pages": f"{s.page_debut}-{s.page_fin}",
                              "car": len(s.extrait)} for s in sel["retenues"]]}
        elif d.role == "arrete":
            faits += faits_arrete(d)            # regex : deuxième témoin
            faits += _faits_fiche_arrete(d)
    faits += faits_classeur
    chronometrer("referentiel", t0)

    etape("reconciliation", "Réconciliation des sources…")
    t0 = time.time()
    ref = reconcilier(faits, zones, couches, docs)
    tri = {}
    for d in docs:
        if d.nom in cartes:
            tri[d.nom] = trier(cartes[d.nom], d, ref)
    chronometrer("reconciliation", t0)
    chronometrer("pipeline", pipeline_t0)
    etape("termine", "Référentiel prêt à valider.")
    tokens = llm.bilan_tokens("passe 1")

    return {
        "config": config.snapshot(),
        "documents": [{"nom": d.nom, "role": d.role, "indice": d.role_indice,
                       "pages": len(d.pages), "pages_scannees": len(d.pages_image),
                       "sous_documents": d.sous_documents,
                       "fiabilite_ocr": {str(p.num): p.fiabilite_ocr for p in d.pages
                                         if p.fiabilite_ocr is not None}} for d in docs],
        "sig": {"couches": [c.model_dump() for c in couches], "zones": [z.model_dump() for z in zones]},
        "selection_pdf": selection,
        "cartes": {k: {"vocabulaire": c.vocabulaire.model_dump(), "nb_fiches": len(c.fiches),
                       "nb_tableaux": len(c.tableaux), "verification": c.verification,
                       "remarques": [r.model_dump() for r in c.remarques],
                       "tri_remarques": {"questions": tri.get(k, {}).get("questions", 0),
                                         "anomalies": len(tri.get(k, {}).get("anomalies", [])),
                                         "internes": len(tri.get(k, {}).get("internes", []))},
                       "remarques_internes": tri.get(k, {}).get("internes", [])}
                   for k, c in cartes.items()},
        "cartes_classeur": vues_classeur,
        "regles": {"source": regles.source, "unite": regles.re_unite.pattern,
                   "code": regles.re_code.pattern, "champs": regles.champs},
        "faits": [f.model_dump() for f in faits],
        "referentiel": ref.model_dump(),
        "durees_s": t,
        "tokens_llm": tokens,
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
