#!/usr/bin/env python3
"""
extract_actions_mistral.py — Fiches-actions intégrales depuis le markdown OCR.

Usage :
    python extract_actions_mistral.py
    python extract_actions_mistral.py ocr_output/full.md --out actions.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from ..mistral_client import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    PRIX,
    PRIX_DEFAUT,
    Compteur,
    extraire_structure,
)
from ..models import ActionsResult
from .prompts.prompt_extract_actions import SYSTEM_PROMPT

_OCR_DIR = Path(__file__).resolve().parent.parent

load_dotenv(_OCR_DIR / ".env")

log = logging.getLogger("pipeline.actions")
DEFAULT_INPUT = _OCR_DIR / "ocr_output" / "full.md"
DEFAULT_OUTPUT = _OCR_DIR / "actions.json"
DEBUG_DIR = _OCR_DIR / "debug" / "actions"


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
) -> ActionsResult:
    log.info("Document complet : %d caractères", len(markdown))

    return extraire_structure(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=(
            "Extrais toutes les fiches-actions du plan complet ci-dessous. "
            "Rappel : `contenu_integral` = texte OCR complet de chaque fiche, sans résumé.\n\n"
            f"<plan_de_gestion>\n{markdown}\n</plan_de_gestion>"
        ),
        result_type=ActionsResult,
        etiquettes="ACTIONS",
        debug_dir=DEBUG_DIR,
        debug_prefixe="actions",
        model=model,
        effort=effort,
        max_tokens=max_tokens,
        utiliser_schema=utiliser_schema,
        compteur=compteur,
        schema_name="extraction_actions",
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Extraction fiches-actions via Mistral.")
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
    log.info("Démarrage extraction actions depuis %s", src)

    resultat = extraire(
        markdown, args.model, args.effort, compteur, args.max_tokens, args.schema,
    )

    out = _resoudre_chemin(args.out)
    if not out.is_absolute() and not Path(args.out).exists():
        out = _OCR_DIR / args.out
    out.write_text(resultat.model_dump_json(indent=2), encoding="utf-8")

    print(f"\n✅ {len(resultat.actions)} action(s) → {out}")
    for a in resultat.actions:
        print(f"   · {a.id:<6} {len(a.contenu_integral):>6,} car. · pages {a.pages or '?'}")

    print(compteur.rapport())

    (_OCR_DIR / "cout_actions.json").write_text(json.dumps({
        "etape": "actions",
        "model": args.model,
        "effort": args.effort,
        "cout_usd": round(compteur.cout, 4),
        "tokens_input": compteur.tok_in,
        "tokens_output": compteur.tok_out,
        "nb_actions": len(resultat.actions),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
