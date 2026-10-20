"""Ligne de commande.

    python -m arx run arrete.pdf [--anchor demarrage_travaux=2025-12-05] [--anchor notification=2025-11-21]
                                 [--mode parallel|single] [-k 1] [--controls] [--figures]
    python -m arx run arrete.pdf --offline --inject examples/bruges/expected_llm.json   # sans API
    python -m arx segment arrete.pdf                                                     # étape 2 seule
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import Settings


def main() -> None:
    ap = argparse.ArgumentParser(prog="arx")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("pdf", type=Path)
    r.add_argument("--anchor", action="append", default=[], help="nom=AAAA-MM-JJ (notification, demarrage_travaux…)")
    r.add_argument("--mode", choices=["parallel", "single"], help="parallel (défaut) ou single (1 appel)")
    r.add_argument("-k", "--samples", type=int, help="échantillons par appel (défaut 1)")
    r.add_argument("--controls", action="store_true", help="ajoute la passe LLM de contradictions (1 appel)")
    r.add_argument("--figures", action="store_true", help="lire les figures avec le modèle vision")
    r.add_argument("--force-ocr", action="store_true")
    r.add_argument("--offline", action="store_true")
    r.add_argument("--inject", type=Path, help="réponses LLM préparées (fiche, synthèse, articles)")
    r.add_argument("--runs-dir", type=Path)
    sg = sub.add_parser("segment")
    sg.add_argument("pdf", type=Path)
    a = ap.parse_args()

    s = Settings()
    if a.cmd == "segment":
        from .llm import MistralClient
        from .s1_ingest import ingest
        from .s2_segment import segment
        s.offline = True
        (s.runs_dir / "_seg").mkdir(parents=True, exist_ok=True)
        doc, _ = ingest(a.pdf, s, MistralClient(s, s.runs_dir / "_seg"))
        g = segment(doc)
        print(f"Réf. {g.reference} · signé {g.signature_lieu} {g.signature_date} · {len(g.visas)} VU · {len(g.considerants)} CONSIDÉRANT")
        for x in g.articles:
            print(f"{'  ' * (x.niveau - 1)}{x.numero:8} p.{x.page_debut}-{x.page_fin}  {x.titre}")
        for f in g.figures:
            print(f"  fig.{f.numero} p.{f.page} (art. {f.article}) {f.legende}")
        for i in g.issues:
            print("!", i)
        return

    if a.samples:
        s.samples_k = a.samples
    s.contradiction_pass = a.controls
    if a.mode:
        s.mode = a.mode
    s.figures_vision = a.figures
    s.force_ocr = a.force_ocr
    s.offline = a.offline
    if a.runs_dir:
        s.runs_dir = a.runs_dir
    anchors = dict(x.split("=", 1) for x in a.anchor)
    from .pipeline import run
    run(a.pdf, s, anchors=anchors, inject=a.inject)


if __name__ == "__main__":
    main()
