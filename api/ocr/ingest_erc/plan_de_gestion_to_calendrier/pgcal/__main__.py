"""Ligne de commande.

    python -m pgcal run  plan.pdf [--n-year 2026] [-k 3] [--no-critic] [--no-vision] [--step-policy max]
    python -m pgcal run  plan.pdf --offline --rules-json eval/gold/bruges_rules.json
    python -m pgcal grids plan.pdf [--pages 24,25,30]       # étape 3 seule, sans API (debug)
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import Settings


def main() -> None:
    ap = argparse.ArgumentParser(prog="pgcal")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="pipeline complet")
    r.add_argument("pdf", type=Path)
    r.add_argument("--n-year", type=int, help="année réelle de N (sinon celle du document)")
    r.add_argument("-k", "--samples", type=int, help="échantillons LLM par action")
    r.add_argument("--no-critic", action="store_true")
    r.add_argument("--no-vision", action="store_true", help="pas de double lecture vision des grilles natives")
    r.add_argument("--step-policy", choices=["min", "max", "mid"])
    r.add_argument("--offline", action="store_true", help="aucun appel API")
    r.add_argument("--rules-json", type=Path, help="injecter cadre + règles (test / comparaison)")
    r.add_argument("--runs-dir", type=Path)

    g = sub.add_parser("grids", help="lire les grilles d'un PDF (géométrie seule)")
    g.add_argument("pdf", type=Path)
    g.add_argument("--pages", help="ex. 24,25,30 (défaut : pages détectées)")

    a = ap.parse_args()
    s = Settings()
    if a.cmd == "grids":
        import pdfplumber
        from .llm import MistralClient
        from .s1_ingest import ingest
        from .s2_locate import locate
        from .s3_grids import grids_as_text, parse_page_geometry
        s.offline = True
        tmp = s.runs_dir / "_grids"
        tmp.mkdir(parents=True, exist_ok=True)
        llm = MistralClient(s, tmp)
        doc, _ = ingest(a.pdf, s, llm)
        dm, _ = locate(doc, s, llm)
        pages = [int(x) for x in a.pages.split(",")] if a.pages else [i.number for i in dm.pages if i.grid_hint]
        fiche = {i.number: i.fiche_code for i in dm.pages}
        with pdfplumber.open(a.pdf) as pdf:
            grids = [gr for n in pages for gr in parse_page_geometry(pdf, n, fiche.get(n))]
        print(grids_as_text(grids))
        return

    if a.n_year:
        s.n_year_override = a.n_year
    if a.samples:
        s.samples_k = a.samples
    if a.no_critic:
        s.use_critic = False
    if a.no_vision:
        s.grid_vision_crosscheck = False
    if a.step_policy:
        s.step_policy = a.step_policy
    if a.runs_dir:
        s.runs_dir = a.runs_dir
    s.offline = a.offline

    from .pipeline import run
    run(a.pdf, s, rules_json=a.rules_json)


if __name__ == "__main__":
    main()
