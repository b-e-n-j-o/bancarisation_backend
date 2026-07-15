#!/usr/bin/env python3
"""
extract_echeances_mistral.py — Étape 2 du pipeline, version Mistral (stack FR).

▲ VERSION INSTRUMENTÉE. Quand la validation échoue, on sait POURQUOI :
  - la sortie brute (réflexion + réponse) est dumpée dans ./debug/
  - l'erreur de validation Pydantic est affichée IMMÉDIATEMENT (avant le retry)
  - `finish_reason` est capturé → distingue une troncature d'un JSON malformé
  - mode `--schema` : contraint le décodage au JSON Schema (structured outputs)

Prérequis :
    pip install httpx pydantic python-dotenv
    export MISTRAL_API_KEY="..."

Usage :
    python extract_echeances_mistral.py                       # prompt + validation
    python extract_echeances_mistral.py --schema              # décodage contraint
    python extract_echeances_mistral.py --limite-chars 8000   # test rapide
    python extract_echeances_mistral.py --effort medium

─── LES 3 CAUSES DE "Sortie invalide" ───────────────────────────────────────
  1. TRONCATURE  → finish_reason == "length". Le JSON s'arrête au milieu.
                   Fix : augmenter --max-tokens.
  2. PRÉAMBULE   → le modèle écrit "Voici le JSON :" avant. Fix : _extraire_json()
                   récupère du premier { au dernier }.
  3. SCHÉMA      → champ manquant/type faux. Fix : --schema (décodage contraint)
                   ou le retry qui réinjecte l'erreur.
  Le dump ./debug/ te dit laquelle des trois.

─── --schema ET LE RAISONNEMENT ─────────────────────────────────────────────
Mistral supporte response_format={"type":"json_schema", ...,"strict":true}, qui
contraint le décodage à coller au schéma. MAIS : le décodage contraint force du
JSON dès le premier token, ce qui est a priori incompatible avec un bloc de
réflexion préalable. Le script bascule donc automatiquement sur effort="none"
quand --schema est actif, sauf si tu forces --effort. À toi de tester lequel
extrait le mieux : raisonnement libre + validation, ou décodage contraint.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

_SCRIPT_DIR = Path(__file__).resolve().parent
_OCR_DIR = _SCRIPT_DIR.parent
if str(_OCR_DIR) not in sys.path:
    sys.path.insert(0, str(_OCR_DIR))
sys.path.insert(0, str(_OCR_DIR / "prompts"))

from models import ExtractionResult

# Prompt système mutualisé avec la version Anthropic : une seule vérité.
from prompt_extract_echeances import SYSTEM_PROMPT

load_dotenv(_OCR_DIR / ".env")

API_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_INPUT = _OCR_DIR / "ocr_output" / "full.md"
DEFAULT_OUTPUT = _OCR_DIR / "echeances_mistral.json"
DEFAULT_MODEL = "mistral-medium-3-5"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TOKENS = 32000
MAX_TENTATIVES = 3
DEBUG_DIR = _OCR_DIR / "debug"

# ⚠️ TARIFS À VÉRIFIER sur https://mistral.ai/pricing (USD / M tokens).
PRIX = {
    "mistral-medium-3-5":   (0.40, 2.00),   # ← à confirmer
    "mistral-small-latest": (0.15, 0.60),
    "mistral-large-latest": (0.50, 1.50),
}


# --- Schéma Mistral ---------------------------------------------------------

def _inliner(noeud, defs: dict):
    """Résout les $ref de Pydantic : Mistral strict ne gère pas les $defs."""
    if isinstance(noeud, dict):
        if "$ref" in noeud:
            nom = noeud["$ref"].split("/")[-1]
            return _inliner(defs[nom], defs)
        return {k: _inliner(v, defs) for k, v in noeud.items() if k != "$defs"}
    if isinstance(noeud, list):
        return [_inliner(v, defs) for v in noeud]
    return noeud


def _durcir(noeud):
    """strict:true exige additionalProperties:false + tous les champs requis."""
    if isinstance(noeud, dict):
        if noeud.get("type") == "object" and "properties" in noeud:
            noeud["additionalProperties"] = False
            noeud["required"] = list(noeud["properties"].keys())
        for v in noeud.values():
            _durcir(v)
    elif isinstance(noeud, list):
        for v in noeud:
            _durcir(v)
    return noeud


def schema_mistral() -> dict:
    """ExtractionResult (Pydantic) → JSON Schema accepté par Mistral strict."""
    brut = ExtractionResult.model_json_schema()
    plat = _inliner(brut, brut.get("$defs", {}))
    return _durcir(plat)


# --- Parsing ----------------------------------------------------------------

def _texte_des_chunks(valeur) -> tuple[str, str]:
    if valeur is None:
        return "", ""
    if isinstance(valeur, str):
        return "", valeur

    reflexion, reponse = [], []
    for chunk in valeur:
        if not isinstance(chunk, dict):
            reponse.append(str(chunk))
            continue
        ctype = chunk.get("type")
        if ctype in ("thinking", "think"):
            inner = chunk.get("thinking") or chunk.get("content") or []
            if isinstance(inner, str):
                reflexion.append(inner)
            else:
                for c in inner:
                    reflexion.append(c.get("text", "") if isinstance(c, dict) else str(c))
        elif ctype == "text":
            reponse.append(chunk.get("text", ""))
    return "".join(reflexion), "".join(reponse)


def _extraire_json(texte: str) -> str:
    """Retire les fences ET tout préambule/postambule autour du JSON."""
    t = texte.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
        t = t.strip().removeprefix("json").strip()
    # Cause n°2 : le modèle bavarde avant/après. On prend du 1er { au dernier }.
    d, f = t.find("{"), t.rfind("}")
    return t[d:f + 1] if d != -1 and f > d else t


def _dump(tentative: int, reflexion: str, reponse: str, err: Exception | None) -> Path:
    DEBUG_DIR.mkdir(exist_ok=True)
    if reflexion:
        (DEBUG_DIR / f"t{tentative}_reflexion.txt").write_text(reflexion, encoding="utf-8")
    cible = DEBUG_DIR / f"t{tentative}_reponse_brute.txt"
    cible.write_text(reponse, encoding="utf-8")
    if err:
        (DEBUG_DIR / f"t{tentative}_erreur.txt").write_text(str(err), encoding="utf-8")
    return cible


# --- Compteur ---------------------------------------------------------------

class Compteur:
    def __init__(self, model: str, prix_in: float, prix_out: float):
        self.model, self.prix_in, self.prix_out = model, prix_in, prix_out
        self.appels = self.tok_in = self.tok_out = 0
        self.car_reflexion = self.car_reponse = 0
        self.usages_bruts: list[dict] = []

    def ajouter(self, usage: dict, reflexion: str, reponse: str) -> None:
        self.appels += 1
        self.usages_bruts.append(usage)
        self.tok_in += usage.get("prompt_tokens", 0)
        self.tok_out += usage.get("completion_tokens", 0)
        self.car_reflexion += len(reflexion)
        self.car_reponse += len(reponse)

    @property
    def cout(self) -> float:
        return self.tok_in / 1e6 * self.prix_in + self.tok_out / 1e6 * self.prix_out

    def tok_reflexion_estime(self) -> int:
        total = self.car_reflexion + self.car_reponse
        return round(self.tok_out * self.car_reflexion / total) if total else 0

    def rapport(self) -> str:
        refl = self.tok_reflexion_estime()
        part = (refl / self.tok_out * 100) if self.tok_out else 0
        return "\n".join([
            "", "─" * 62,
            f"  COÛT — {self.model}",
            "─" * 62,
            f"  Appels API                 {self.appels}",
            f"  Tokens input               {self.tok_in:>10,}   ${self.tok_in/1e6*self.prix_in:.4f}",
            f"  Tokens output (total)      {self.tok_out:>10,}   ${self.tok_out/1e6*self.prix_out:.4f}",
            f"    ├─ réflexion (estimé)    {refl:>10,}   ${refl/1e6*self.prix_out:.4f}  ({part:.0f}% de l'output)",
            f"    └─ réponse JSON          {self.tok_out - refl:>10,}",
            "─" * 62,
            f"  TOTAL                                    ${self.cout:.4f}  (~{self.cout*0.92:.4f} €)",
            "─" * 62,
            f"  Tarifs : ${self.prix_in}/M in · ${self.prix_out}/M out — vérifie mistral.ai/pricing",
            "",
            f"  usage brut : {json.dumps(self.usages_bruts[-1], ensure_ascii=False) if self.usages_bruts else '—'}",
        ])


# --- Appel streamé ----------------------------------------------------------

def _appel_streame(client: httpx.Client, api_key: str, payload: dict):
    """Retourne (reflexion, reponse, usage, finish_reason)."""
    reflexion: list[str] = []
    reponse: list[str] = []
    usage: dict = {}
    finish: str | None = None
    t0 = time.time()
    premier = None
    en_reflexion = False
    dernier = 0.0

    with client.stream(
        "POST", API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
    ) as r:
        if r.status_code != 200:
            corps = b"".join(r.iter_bytes()).decode(errors="replace")
            sys.exit(f"\n❌ HTTP {r.status_code} : {corps[:800]}")

        for ligne in r.iter_lines():
            if not ligne.startswith("data: "):
                continue
            data = ligne[6:]
            if data.strip() == "[DONE]":
                break
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue

            if evt.get("usage"):
                usage = evt["usage"]

            choix = (evt.get("choices") or [{}])[0]
            if choix.get("finish_reason"):
                finish = choix["finish_reason"]

            delta = choix.get("delta", {}).get("content")
            if not delta:
                continue

            if premier is None:
                premier = time.time() - t0
                print(f"   ⏱️  Premier token après {premier:.1f}s — ça vit.")

            d_refl, d_rep = _texte_des_chunks(delta)
            if d_refl:
                if not en_reflexion:
                    print("   🧠 réflexion en cours …")
                    en_reflexion = True
                reflexion.append(d_refl)
            if d_rep:
                if en_reflexion:
                    print(f"\n   ✍️  réflexion terminée ({len(''.join(reflexion)):,} car.), "
                          "rédaction du JSON …")
                    en_reflexion = False
                reponse.append(d_rep)

            maintenant = time.time()
            if maintenant - dernier > 2.0:
                dernier = maintenant
                ecoule = maintenant - t0
                car = len("".join(reflexion)) + len("".join(reponse))
                phase = "réflexion" if en_reflexion else "JSON"
                print(f"\r   {ecoule:5.0f}s · {car:>7,} car. · "
                      f"{car/max(ecoule,1):>5.0f} car/s · {phase}    ", end="", flush=True)

    print(f"\r   ✅ {time.time() - t0:.0f}s au total" + " " * 45)
    return "".join(reflexion), "".join(reponse), usage, finish


# --- Extraction -------------------------------------------------------------

def extraire(
    markdown: str,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    compteur: Compteur | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    utiliser_schema: bool = False,
) -> ExtractionResult:
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        sys.exit("❌ MISTRAL_API_KEY absente.")

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            "Voici le plan de gestion OCRisé. Extrais toutes les échéances "
            f"selon les règles.\n\n<plan_de_gestion>\n{markdown}\n</plan_de_gestion>"
        )},
    ]

    derniere_erreur: Exception | None = None

    with httpx.Client(timeout=httpx.Timeout(30.0, read=1800.0)) as client:
        for tentative in range(1, MAX_TENTATIVES + 1):
            print(f"\n🤖 {model} · effort={effort} · schema={utiliser_schema} "
                  f"· tentative {tentative}/{MAX_TENTATIVES}")

            payload: dict = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stream": True,
            }
            if effort != "none":
                payload["reasoning_effort"] = effort
            if utiliser_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "extraction_echeances",
                        "schema": schema_mistral(),
                        "strict": True,
                    },
                }

            reflexion, reponse, usage, finish = _appel_streame(client, api_key, payload)

            if compteur:
                compteur.ajouter(usage, reflexion, reponse)
            print(f"   in={usage.get('prompt_tokens', 0):,} "
                  f"out={usage.get('completion_tokens', 0):,} "
                  f"· finish_reason={finish} · réponse={len(reponse):,} car.")

            if finish == "length":
                print(f"   🔪 TRONQUÉ : le modèle a atteint max_tokens ({max_tokens:,}). "
                      f"Relance avec --max-tokens {max_tokens * 2}.")

            try:
                resultat = ExtractionResult.model_validate_json(_extraire_json(reponse))
                _dump(tentative, reflexion, reponse, None)
                return resultat
            except (ValidationError, json.JSONDecodeError) as err:
                derniere_erreur = err
                chemin = _dump(tentative, reflexion, reponse, err)
                print(f"\n   ⚠️  VALIDATION ÉCHOUÉE — brut dumpé dans {chemin}")
                print("   ┌─ erreur (500 premiers car.) " + "─" * 30)
                for l in str(err)[:500].splitlines():
                    print(f"   │ {l}")
                print("   └" + "─" * 58)
                print(f"   ┌─ début de la réponse brute " + "─" * 31)
                for l in reponse[:300].splitlines()[:8]:
                    print(f"   │ {l}")
                print("   └" + "─" * 58)

                if tentative < MAX_TENTATIVES:
                    print("   ↻ réinjection de l'erreur au modèle …")
                    messages += [
                        {"role": "assistant", "content": reponse},
                        {"role": "user", "content": (
                            "Ta réponse n'est pas conforme au schéma. Erreur de validation :\n"
                            f"{err}\n\nRenvoie UNIQUEMENT le JSON corrigé, complet, "
                            "sans commentaire ni balise markdown."
                        )},
                    ]

    raise SystemExit(
        f"\n❌ Extraction invalide après {MAX_TENTATIVES} tentatives.\n"
        f"   Inspecte {DEBUG_DIR}/ — les fichiers t*_reponse_brute.txt contiennent "
        f"ce que le modèle a réellement renvoyé.\n   Dernière erreur : {derniere_erreur}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Extraction des échéances via Mistral.")
    p.add_argument("markdown", nargs="?", default=str(DEFAULT_INPUT))
    p.add_argument("--out", default=str(DEFAULT_OUTPUT))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default=None, choices=["none", "low", "medium", "high"])
    p.add_argument("--schema", action="store_true",
                   help="Décodage contraint par le JSON Schema (structured outputs)")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--limite-chars", type=int, default=None)
    p.add_argument("--prix-in", type=float, default=None)
    p.add_argument("--prix-out", type=float, default=None)
    p.add_argument("--dump-schema", action="store_true",
                   help="Affiche le JSON Schema envoyé à Mistral et sort")
    args = p.parse_args()

    if args.dump_schema:
        print(json.dumps(schema_mistral(), ensure_ascii=False, indent=2))
        return

    # Le décodage contraint force du JSON dès le 1er token → pas de bloc de
    # réflexion préalable. On désactive l'effort sauf demande explicite.
    if args.effort is None:
        effort = "none" if args.schema else DEFAULT_EFFORT
    else:
        effort = args.effort
        if args.schema and effort != "none":
            print("⚠️  --schema + effort != none : combinaison non garantie par Mistral.")

    src = Path(args.markdown)
    if not src.exists():
        sys.exit(f"❌ Markdown introuvable : {src}")

    markdown = src.read_text(encoding="utf-8")
    if args.limite_chars:
        markdown = markdown[:args.limite_chars]
        print(f"✂️  Tronqué à {args.limite_chars:,} caractères (extraction partielle)")

    defaut = PRIX.get(args.model, (0.40, 2.00))
    compteur = Compteur(
        args.model,
        args.prix_in if args.prix_in is not None else defaut[0],
        args.prix_out if args.prix_out is not None else defaut[1],
    )

    print(f"📄 {src.name} — {len(markdown):,} caractères (~{len(markdown)/3.5:,.0f} tokens)")

    resultat = extraire(markdown, args.model, effort, compteur, args.max_tokens, args.schema)

    Path(args.out).write_text(resultat.model_dump_json(indent=2), encoding="utf-8")

    ech = resultat.echeances
    a_revoir = [e for e in ech if e.confiance < 0.7 or e.champs_a_confirmer or e.avertissements]
    print(f"\n✅ {len(ech)} échéance(s) → {args.out}")
    print(f"   {len(a_revoir)} à faire valider :")
    for e in a_revoir:
        flags = []
        if e.confiance < 0.7:
            flags.append(f"confiance {e.confiance:.2f}")
        if e.champs_a_confirmer:
            flags.append("à confirmer: " + ", ".join(e.champs_a_confirmer))
        if e.avertissements:
            flags.append(f"{len(e.avertissements)} avertissement(s)")
        print(f"   · {e.id:<42} {' | '.join(flags)}")

    print(compteur.rapport())

    Path("./cout_extraction.json").write_text(json.dumps({
        "model": args.model,
        "reasoning_effort": effort,
        "schema": args.schema,
        "chars_input": len(markdown),
        "appels": compteur.appels,
        "tokens_input": compteur.tok_in,
        "tokens_output": compteur.tok_out,
        "tokens_reflexion_estime": compteur.tok_reflexion_estime(),
        "cout_usd": round(compteur.cout, 4),
        "echeances": len(ech),
        "a_revoir": len(a_revoir),
        "usages_bruts": compteur.usages_bruts,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("   Détail machine → ./cout_extraction.json")


if __name__ == "__main__":
    main()