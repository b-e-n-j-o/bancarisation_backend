#!/usr/bin/env python3
"""
extract_echeances.py — Étape 2 du pipeline : markdown OCR → échéances JSON.

Fait suite à test_ocr.py (étape 1, Mistral OCR 4) qui produit ./ocr_output/full.md.

Prérequis :
    pip install anthropic pydantic python-dotenv
    export ANTHROPIC_API_KEY="..."

Usage :
    python extract_echeances.py                                  # ./ocr_output/full.md
    python extract_echeances.py chemin/vers/full.md
    python extract_echeances.py --out echeances.json --model claude-sonnet-5

Sortie : echeances.json, conforme à models.ExtractionResult.

Pourquoi le document ENTIER en un seul appel (et pas page par page) :
  - les fiches-actions enjambent les pages (TU1 = p.15 à 17) ;
  - les contradictions à détecter sont inter-pages (le rythme SE1 est écrit
    différemment p.6, p.26 et p.29) ;
  - les marqueurs `<!-- ===== PAGE N ===== -->` injectés par test_ocr.py
    permettent au modèle de renseigner source.page tout seul.
Pour un document très long (>150 p.), découper par fiche, jamais par page.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from extract_echeances import SYSTEM_PROMPT
from models import ExtractionResult

load_dotenv()

DEFAULT_INPUT = Path("./ocr_output/full.md")
DEFAULT_OUTPUT = Path("./echeances.json")

# Extraction réglementaire nuancée + détection de contradictions → tier Opus.
# Alternative moins chère et souvent suffisante sur des PDF bien structurés :
# "claude-sonnet-5".
DEFAULT_MODEL = "claude-opus-4-8"

MAX_TENTATIVES = 3


def _nettoyer_json(texte: str) -> str:
    t = texte.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip().removeprefix("json").strip()


def extraire(markdown: str, model: str = DEFAULT_MODEL) -> ExtractionResult:
    """Appelle le LLM, valide la sortie, retente en réinjectant l'erreur."""
    client = Anthropic()
    messages: list[dict] = [{
        "role": "user",
        "content": (
            "Voici le plan de gestion OCRisé. Extrais toutes les échéances "
            f"selon les règles.\n\n<plan_de_gestion>\n{markdown}\n</plan_de_gestion>"
        ),
    }]

    derniere_erreur: Exception | None = None

    for tentative in range(1, MAX_TENTATIVES + 1):
        print(f"🤖 Extraction ({model}) — tentative {tentative}/{MAX_TENTATIVES} …")
        resp = client.messages.create(
            model=model,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        brut = "".join(b.text for b in resp.content if b.type == "text")

        try:
            return ExtractionResult.model_validate_json(_nettoyer_json(brut))
        except (ValidationError, json.JSONDecodeError) as err:
            derniere_erreur = err
            print(f"   ⚠️  Sortie invalide, réinjection de l'erreur au modèle.")
            messages += [
                {"role": "assistant", "content": brut},
                {"role": "user", "content": (
                    "Ta réponse n'est pas conforme au schéma. Erreur de validation :\n"
                    f"{err}\n\nRenvoie UNIQUEMENT le JSON corrigé, complet, sans commentaire."
                )},
            ]

    raise SystemExit(f"❌ Extraction invalide après {MAX_TENTATIVES} tentatives : {derniere_erreur}")


def main() -> None:
    p = argparse.ArgumentParser(description="Extraction LLM des échéances depuis le markdown OCR.")
    p.add_argument("markdown", nargs="?", default=str(DEFAULT_INPUT), help="Chemin du full.md")
    p.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Fichier JSON de sortie")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Modèle Anthropic")
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("❌ ANTHROPIC_API_KEY absente.")

    src = Path(args.markdown)
    if not src.exists():
        sys.exit(f"❌ Markdown introuvable : {src} (lance d'abord test_ocr.py)")

    markdown = src.read_text(encoding="utf-8")
    print(f"📄 {src.name} — {len(markdown):,} caractères")

    resultat = extraire(markdown, args.model)

    Path(args.out).write_text(
        resultat.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
    )

    # Résumé console orienté revue humaine.
    ech = resultat.echeances
    a_revoir = [e for e in ech if e.confiance < 0.7 or e.champs_a_confirmer or e.avertissements]
    print(f"\n✅ {len(ech)} échéance(s) extraite(s) → {args.out}")
    print(f"   {len(a_revoir)} nécessite(nt) une revue humaine :")
    for e in a_revoir:
        flags = []
        if e.confiance < 0.7:
            flags.append(f"confiance {e.confiance:.2f}")
        if e.champs_a_confirmer:
            flags.append("à confirmer: " + ", ".join(e.champs_a_confirmer))
        if e.avertissements:
            flags.append(f"{len(e.avertissements)} avertissement(s)")
        print(f"   · {e.id:<42} {' | '.join(flags)}")


if __name__ == "__main__":
    main()