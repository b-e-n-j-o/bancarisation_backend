#!/usr/bin/env python3
"""
smoke_test_mistral.py — Vérif de plomberie en ~30 secondes.

Répond à : "est-ce que ma clé marche, est-ce que le modèle existe, est-ce que
reasoning_effort passe, et à quelle vitesse ce modèle génère ?"

    python smoke_test_mistral.py
    python smoke_test_mistral.py --model small --effort none
    python smoke_test_mistral.py --model medium --effort high
    python smoke_test_mistral.py --model large --effort low
    python smoke_test_mistral.py --model glm --effort high
    python smoke_test_mistral.py --model glm --reasoning xhigh

La vitesse mesurée (tokens/s) permet d'extrapoler la durée du vrai run :
    durée ≈ tokens_output_attendus / vitesse
Sur un plan de gestion (~13k tokens in, ~15-25k tokens out avec effort=high),
compte plusieurs minutes. C'est normal.
"""

import argparse
import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://api.mistral.ai/v1/chat/completions"

# Alias CLI → id API Mistral.
ALIAS = {
    "small": "mistral-small-latest",
    "medium": "mistral-medium-3-5",
    "large": "mistral-large-latest",
    "glm": "zai-glm-5-2",
}

# Variantes d'écriture courantes → id canonique.
NORMALISATION = {
    "mistral-medium-3.5": "mistral-medium-3-5",
    "mistral-medium-3_5": "mistral-medium-3-5",
    "mistral-medium-latest": "mistral-medium-3-5",
}

# USD / million de tokens : (input, cached_input, output).
# GLM : tarifs officiels Mistral (août 2026). Autres : mistral.ai/pricing.
PRIX = {
    "mistral-small-latest": (0.15, 0.015, 0.60),
    "mistral-small-2603": (0.15, 0.015, 0.60),
    "mistral-medium-3-5": (1.50, 0.15, 7.50),
    "mistral-medium-latest": (1.50, 0.15, 7.50),
    "mistral-medium-2604": (1.50, 0.15, 7.50),
    "mistral-large-latest": (0.50, 0.05, 1.50),
    "mistral-large-2411": (0.50, 0.05, 1.50),
    "zai-glm-5-2": (1.40, 0.14, 4.40),
    "glm-5-2": (1.40, 0.14, 4.40),
}

PLAN_IN = 13_000
PLAN_OUT = 20_000

# Enum officiel Mistral /v1/chat/completions : none|minimal|low|medium|high|xhigh
# (pas de champ `thinking` : c'est Z.ai only, 422 extra_forbidden chez Mistral).
# `max` est un alias GLM/Z.ai → `xhigh` côté Mistral.
EFFORTS_TOUS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
EFFORTS_MISTRAL = ("none", "low", "medium", "high")
EFFORTS_GLM = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
ALIAS_EFFORT = {"max": "xhigh"}


def resoudre_modele(choix: str) -> str:
    cle = choix.strip()
    alias = ALIAS.get(cle.lower())
    if alias:
        return alias
    return NORMALISATION.get(cle, NORMALISATION.get(cle.lower(), cle))


def famille(model: str) -> str:
    m = model.lower()
    if "glm" in m or "zai" in m:
        return "glm"
    if "small" in m:
        return "small"
    if "medium" in m:
        return "medium"
    if "large" in m:
        return "large"
    return "autre"


def efforts_pour(model: str) -> tuple[str, ...]:
    return EFFORTS_GLM if famille(model) == "glm" else EFFORTS_MISTRAL


def effort_api(effort: str) -> str:
    return ALIAS_EFFORT.get(effort, effort)


def payload_raisonnement(_model: str, effort: str) -> dict:
    """Chez Mistral : uniquement reasoning_effort. Jamais `thinking` (Z.ai)."""
    return {"reasoning_effort": effort_api(effort)}


def effort_refuse(corps: str) -> bool:
    c = corps.lower()
    return "reasoning_effort" in c and any(
        k in c for k in ("not supported", "extra_forbidden", "invalid", "not permitted")
    )


def tarifs_pour(model: str) -> tuple[float, float, float] | None:
    if model in PRIX:
        return PRIX[model]
    m = model.lower()
    if "glm" in m:
        return PRIX["zai-glm-5-2"]
    if "small" in m:
        return PRIX["mistral-small-latest"]
    if "medium" in m:
        return PRIX["mistral-medium-3-5"]
    if "large" in m:
        return PRIX["mistral-large-latest"]
    return None


def tokens_usage(usage: dict) -> tuple[int, int, int]:
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached = 0
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    cached = int(usage.get("prompt_cache_hit_tokens") or cached or 0)
    return prompt, cached, completion


def cout_usd(prompt: int, cached: int, completion: int,
             prix: tuple[float, float, float]) -> float:
    pin, pcached, pout = prix
    non_cached = max(prompt - cached, 0)
    return (non_cached / 1e6) * pin + (cached / 1e6) * pcached + (completion / 1e6) * pout


def fmt_usd(montant: float) -> str:
    if montant < 0.01:
        return f"${montant:.4f}"
    return f"${montant:.3f}"


def streamer(client: httpx.Client, api_key: str, payload: dict, t0: float
             ) -> tuple[bool, int, str, dict, float | None, int]:
    premier_token = None
    morceaux = 0
    usage: dict = {}
    with client.stream(
        "POST", API_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json=payload,
    ) as r:
        if r.status_code != 200:
            corps = b"".join(r.iter_bytes()).decode(errors="replace")
            return False, r.status_code, corps, {}, None, 0
        for ligne in r.iter_lines():
            if not ligne.startswith("data: "):
                continue
            data = ligne[6:]
            if data.strip() == "[DONE]":
                break
            evt = json.loads(data)
            if evt.get("usage"):
                usage = evt["usage"]
            delta = (evt.get("choices") or [{}])[0].get("delta", {}).get("content")
            if delta:
                if premier_token is None:
                    premier_token = time.time() - t0
                    print(f"   ⏱️  Premier token après {premier_token:.1f}s "
                          f"→ ça vit.")
                morceaux += 1
                print(".", end="", flush=True)
    return True, 200, "", usage, premier_token, morceaux


def main() -> None:
    p = argparse.ArgumentParser(
        description="Smoke test Mistral : clé, modèle, vitesse, coût estimé.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Modèles : small, medium, large, glm  (ou un id API exact).\n"
            "Raisonnement : --effort / --reasoning\n"
            "  · small/medium/large : none, low, medium, high\n"
            "  · glm (API Mistral)  : none, minimal, low, medium, high, xhigh\n"
            "    (max = alias de xhigh ; le champ Z.ai `thinking` est refusé)"
        ),
    )
    p.add_argument(
        "--model", default="medium",
        help="Alias (small|medium|large|glm) ou id API. Défaut: medium.",
    )
    p.add_argument(
        "--effort", "--reasoning", dest="effort", default="none",
        choices=list(EFFORTS_TOUS),
        help="Mode de raisonnement. Défaut: none. Alias: --reasoning.",
    )
    args = p.parse_args()

    model = resoudre_modele(args.model)
    if args.model.lower() in ALIAS:
        print(f"🧭 Alias '{args.model}' → {model}")

    autorises = efforts_pour(model)
    if args.effort not in autorises:
        print(
            f"⚠️  effort='{args.effort}' n'est pas un palier de {famille(model)} "
            f"({', '.join(autorises)}). On l'envoie quand même."
        )
    print(
        f"🧠 Raisonnement : {args.effort}"
        + (f" → {effort_api(args.effort)}" if effort_api(args.effort) != args.effort else "")
        + f"  (valeurs typiques : {', '.join(autorises)})"
    )
    if famille(model) == "glm":
        print("   GLM chez Mistral : pas de `thinking` (Z.ai). "
              "On envoie seulement reasoning_effort.")

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        sys.exit("❌ MISTRAL_API_KEY absente.")
    print(f"🔑 Clé présente ({api_key[:6]}…{api_key[-4:]})")

    prix = tarifs_pour(model)
    if prix:
        pin, pcached, pout = prix
        print(f"💲 Tarifs {model} : ${pin}/M in · ${pcached}/M cached · ${pout}/M out")
    else:
        print(f"⚠️  Pas de tarif connu pour '{model}' — le coût ne sera pas estimé.")

    # 1. Le modèle existe-t-il pour cette clé ?
    print("\n① Modèles disponibles …")
    r = httpx.get(
        "https://api.mistral.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    if r.status_code != 200:
        sys.exit(f"❌ HTTP {r.status_code} : {r.text[:300]}\n   → clé invalide ?")

    ids = sorted(m["id"] for m in r.json().get("data", []))
    interessants = [i for i in ids if any(
        k in i for k in ("medium", "small", "large", "magistral", "ocr", "glm", "zai"))]
    for i in interessants:
        marque = " ← ton modèle" if i == model else ""
        print(f"   · {i}{marque}")

    if model not in ids:
        print(f"\n⚠️  '{model}' N'EST PAS dans la liste ci-dessus.")
        print("   C'est probablement ta panne. Prends un id exact de la liste.")

    # 2. Appel réel, en streaming, avec chrono.
    extra_raisonnement = payload_raisonnement(model, args.effort)
    print(f"\n② Appel test ({model}, {extra_raisonnement}) …")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content":
                      "Compte de 1 à 20 en français, puis réponds uniquement : OK."}],
        "max_tokens": 2000,
        "stream": True,
        **extra_raisonnement,
    }

    t0 = time.time()
    try:
        with httpx.Client(timeout=180.0) as client:
            ok, status, corps, usage, premier_token, morceaux = streamer(
                client, api_key, payload, t0)
            if not ok and "reasoning_effort" in payload and effort_refuse(corps):
                print(f"\n⚠️  HTTP {status} : reasoning_effort refusé par ce modèle.")
                print(f"   {corps[:300]}")
                print("   Nouvel essai sans reasoning_effort …")
                payload.pop("reasoning_effort", None)
                t0 = time.time()
                ok, status, corps, usage, premier_token, morceaux = streamer(
                    client, api_key, payload, t0)
                if ok:
                    print("\n   → ce modèle n'accepte pas reasoning_effort "
                          "sur l'API Mistral.")
            if not ok:
                sys.exit(f"❌ HTTP {status} : {corps[:500]}")
    except httpx.TimeoutException:
        sys.exit("\n❌ Timeout. Le modèle ne répond pas.")

    duree = time.time() - t0
    prompt, cached, out = tokens_usage(usage)

    print(f"\n\n✅ Terminé en {duree:.1f}s — {morceaux} morceaux reçus")
    print(f"   usage : {json.dumps(usage, ensure_ascii=False)}")

    if prix and (prompt or out):
        cout_run = cout_usd(prompt, cached, out, prix)
        detail_cache = f" dont {cached:,} cached" if cached else ""
        print(f"   coût de ce test ≈ {fmt_usd(cout_run)} "
              f"({prompt:,} in{detail_cache} + {out:,} out)")

    if out and duree:
        vitesse = out / duree
        print(f"   vitesse ≈ {vitesse:.0f} tokens/s")
        print(f"\n   → Extrapolation sur un plan de gestion "
              f"(~{PLAN_IN:,} in + ~{PLAN_OUT:,} out, effort={args.effort}) :")
        print(f"      environ {PLAN_OUT / vitesse / 60:.1f} minute(s). "
              f"Si c'est long, c'est normal.")
        if prix:
            cout_plan = cout_usd(PLAN_IN, 0, PLAN_OUT, prix)
            cout_plan_cache = cout_usd(PLAN_IN, PLAN_IN, PLAN_OUT, prix)
            print(f"      coût ≈ {fmt_usd(cout_plan)} "
                  f"(1er passage, sans cache)")
            print(f"      coût ≈ {fmt_usd(cout_plan_cache)} "
                  f"(si tout l'input est en cache)")


if __name__ == "__main__":
    main()
