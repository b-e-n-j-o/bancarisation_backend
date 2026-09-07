"""
run.py — Orchestrateur de la chaîne d'analyse d'un dossier BE.

    python3 -m api.ocr.multidocs.run /chemin/vers/dossier_BE --out analyse_out/
    python3 -m api.ocr.multidocs.run dossier/ --executer          # lance les jobs
    python3 -m api.ocr.multidocs.run dossier/ --jusqu-a triage    # s'arrête au triage

Étapes :
    1. normalisation  → corpus.json
    2. triage         → carte.json
    3. plan           → plan.json
    4. extraction     → claims.jsonl
    5. rattrapage     → superviseur.json + dossier.json (horizon T0)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .etape1_normalisation import normaliser_dossier
from .etape2_triage import cartographier
from .etape3_planification import planifier, resume
from .etape4_extraction import catalogue, executer
from .superviseur import rattraper

ETAPES = ("normalisation", "triage", "plan", "extraction", "rattrapage")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dossier")
    p.add_argument("--out", default="analyse_out")
    p.add_argument("--jusqu-a", default="plan", choices=ETAPES)
    p.add_argument("--executer", action="store_true", help="équivaut à --jusqu-a rattrapage")
    p.add_argument("--cache-ocr", default=None, help="dossier de cache OCR (fortement conseillé)")
    p.add_argument("--catalogue", action="store_true", help="affiche le registre et sort")
    args = p.parse_args()

    if args.catalogue:
        # Charge les extracteurs pour peupler le registre avant affichage.
        from . import extracteurs  # noqa: F401
        print(catalogue())
        return

    jusqu_a = "rattrapage" if args.executer else args.jusqu_a
    sortie = Path(args.out)
    sortie.mkdir(parents=True, exist_ok=True)
    debug_dir = sortie / "debug"
    cache = Path(args.cache_ocr) if args.cache_ocr else sortie / "cache_ocr"

    # --- 1. normalisation --------------------------------------------------
    corpus, laisses = normaliser_dossier(Path(args.dossier), cache_dir=cache)
    (sortie / "corpus.json").write_text(corpus.model_dump_json(indent=2), encoding="utf-8")
    print(f"📁 {len(corpus.documents)} document(s) normalisé(s), "
          f"{sum(len(d.blocs) for d in corpus.documents)} blocs")
    for l in laisses:
        print(f"   ✗ non traité : {l}")
    if jusqu_a == "normalisation":
        return

    # --- 2. triage ---------------------------------------------------------
    carte = cartographier(corpus, debug_dir=debug_dir)
    (sortie / "carte.json").write_text(carte.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n🗺  {len(carte.segments)} segment(s) cartographié(s)")
    for s in carte.segments:
        print(f"   {s.doc_id:<28} {s.role.value:<26} {s.confiance:.2f}  {s.description[:60]}")
    for prm in carte.parametres:
        print(f"   · {prm.cle} = {prm.valeur}  ({prm.confiance:.2f})")
    for sig in carte.signaux:
        print(f"   ⚠ {sig.type} : {sig.description}")
    if jusqu_a == "triage":
        return

    # --- 3. plan -----------------------------------------------------------
    plan = planifier(carte, corpus)
    (sortie / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    print("\n" + resume(plan))
    if jusqu_a == "plan":
        return

    # --- 4. extraction -----------------------------------------------------
    claims = executer(plan, corpus, carte, sortie, debug_dir)
    par_kind: dict[str, int] = {}
    for c in claims:
        par_kind[c.kind.value] = par_kind.get(c.kind.value, 0) + 1
    print(f"\n✅ {len(claims)} claim(s) → {sortie / 'claims.jsonl'}")
    for k, n in sorted(par_kind.items()):
        print(f"   {k:<22} {n}")
    if jusqu_a == "extraction":
        return

    # --- 5. rattrapage -----------------------------------------------------
    claims, recap_sup = rattraper(claims, corpus, carte, debug_dir=debug_dir, sortie=sortie)
    n_corr = len(recap_sup.get("corrections") or [])
    n_rest = len(recap_sup.get("trous_restants") or [])
    print(f"\n🔧 superviseur : {n_corr} correction(s), {n_rest} trou(s) restant(s)")
    par_kind = {}
    for c in claims:
        par_kind[c.kind.value] = par_kind.get(c.kind.value, 0) + 1
    for k, n in sorted(par_kind.items()):
        print(f"   {k:<22} {n}")


if __name__ == "__main__":
    main()
