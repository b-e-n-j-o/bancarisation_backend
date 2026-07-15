#!/usr/bin/env python3
"""
extract_dossier_mistral.py — Métadonnées projet depuis le markdown OCR.

Usage :
    python extract_dossier_mistral.py
    python extract_dossier_mistral.py ocr_output/full.md --out dossier.json
    python extract_dossier_mistral.py --effort medium --limite-chars 20000
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).resolve().parent
_OCR_DIR = _SCRIPT_DIR.parent
if str(_OCR_DIR) not in sys.path:
    sys.path.insert(0, str(_OCR_DIR))
sys.path.insert(0, str(_OCR_DIR / "prompts"))

from mistral_client import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    PRIX,
    PRIX_DEFAUT,
    Compteur,
    extraire_structure,
)
from models import DossierResult
from prompt_extract_dossier import SYSTEM_PROMPT

load_dotenv(_OCR_DIR / ".env")

log = logging.getLogger("pipeline.dossier")
DEFAULT_INPUT = _OCR_DIR / "ocr_output" / "full.md"
DEFAULT_OUTPUT = _OCR_DIR / "dossier.json"
DEBUG_DIR = _OCR_DIR / "debug" / "dossier"


def _resoudre_chemin(chemin: str | Path) -> Path:
    p = Path(chemin)
    if p.is_absolute():
        return p
    if p.exists():
        return p.resolve()
    candidat = _OCR_DIR / p
    return candidat if candidat.exists() else p


def extraire(
    markdown: str,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    compteur: Compteur | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    utiliser_schema: bool = False,
) -> DossierResult:
    log.info("Document complet : %d caractères", len(markdown))

    return extraire_structure(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=(
            "Extrais les métadonnées du dossier de compensation depuis le plan "
            "complet ci-dessous.\n\n"
            f"<plan_de_gestion>\n{markdown}\n</plan_de_gestion>"
        ),
        result_type=DossierResult,
        etiquettes="DOSSIER",
        debug_dir=DEBUG_DIR,
        debug_prefixe="dossier",
        model=model,
        effort=effort,
        max_tokens=max_tokens,
        utiliser_schema=utiliser_schema,
        compteur=compteur,
        schema_name="extraction_dossier",
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Extraction métadonnées dossier via Mistral.")
    p.add_argument("markdown", nargs="?", default=str(DEFAULT_INPUT))
    p.add_argument("--out", default=str(DEFAULT_OUTPUT))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default=DEFAULT_EFFORT,
                   choices=["none", "low", "medium", "high"])
    p.add_argument("--schema", action="store_true")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--limite-chars", type=int, default=None)
    p.add_argument("--prix-in", type=float, default=None)
    p.add_argument("--prix-out", type=float, default=None)
    args = p.parse_args()

    src = _resoudre_chemin(args.markdown)
    if not src.exists():
        sys.exit(f"❌ Markdown introuvable : {src}")

    markdown = src.read_text(encoding="utf-8")
    if args.limite_chars:
        markdown = markdown[:args.limite_chars]

    defaut = PRIX.get(args.model, PRIX_DEFAUT)
    compteur = Compteur(
        args.model,
        args.prix_in if args.prix_in is not None else defaut[0],
        args.prix_out if args.prix_out is not None else defaut[1],
    )

    print(f"📄 {src.name} — {len(markdown):,} caractères")
    log.info("Démarrage extraction dossier depuis %s", src)

    resultat = extraire(
        markdown, args.model, args.effort, compteur, args.max_tokens, args.schema,
    )

    out = _resoudre_chemin(args.out)
    if not out.is_absolute() and not Path(args.out).exists():
        out = _OCR_DIR / args.out
    out.write_text(resultat.model_dump_json(indent=2), encoding="utf-8")

    d = resultat.dossier
    h = d.horizon
    print(f"\n✅ Dossier extrait → {out}")
    print(f"   Opération      {d.nom_operation or '—'}")
    print(f"   Horizon        {h.annee_debut or '?'} → {h.annee_fin or '?'} ({h.duree_ans or '?'} ans)")
    print(f"   UG             {len(d.unites_gestion)} · Zones {len(d.zones)}")
    if d.champs_a_confirmer or h.champs_a_confirmer:
        print(f"   À confirmer    {', '.join(d.champs_a_confirmer + h.champs_a_confirmer)}")
    if d.avertissements or h.avertissements:
        print(f"   Avertissements {len(d.avertissements) + len(h.avertissements)}")

    print(compteur.rapport())

    (_OCR_DIR / "cout_dossier.json").write_text(json.dumps({
        "etape": "dossier",
        "model": args.model,
        "effort": args.effort,
        "cout_usd": round(compteur.cout, 4),
        "tokens_input": compteur.tok_in,
        "tokens_output": compteur.tok_out,
        "horizon": h.model_dump(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
