#!/usr/bin/env python3
"""
smoke_test_mistral.py — Vérif de plomberie en ~30 secondes.

Répond à : "est-ce que ma clé marche, est-ce que le modèle existe, est-ce que
reasoning_effort passe, et à quelle vitesse ce modèle génère ?"

    python smoke_test_mistral.py
    python smoke_test_mistral.py --model mistral-small-latest

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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="mistral-medium-3-5")
    p.add_argument("--effort", default="high", choices=["none", "low", "medium", "high"])
    args = p.parse_args()

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        sys.exit("❌ MISTRAL_API_KEY absente.")
    print(f"🔑 Clé présente ({api_key[:6]}…{api_key[-4:]})")

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
        k in i for k in ("medium", "small", "large", "magistral", "ocr"))]
    for i in interessants:
        marque = " ← ton modèle" if i == args.model else ""
        print(f"   · {i}{marque}")

    if args.model not in ids:
        print(f"\n⚠️  '{args.model}' N'EST PAS dans la liste ci-dessus.")
        print("   C'est probablement ta panne. Prends un id exact de la liste.")

    # 2. Appel réel, en streaming, avec chrono.
    print(f"\n② Appel test ({args.model}, reasoning_effort={args.effort}) …")
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content":
                      "Compte de 1 à 20 en français, puis réponds uniquement : OK."}],
        "max_tokens": 2000,
        "stream": True,
    }
    if args.effort != "none":
        payload["reasoning_effort"] = args.effort

    t0 = time.time()
    premier_token = None
    morceaux = 0
    usage = {}

    try:
        with httpx.Client(timeout=180.0) as client:
            with client.stream(
                "POST", API_URL,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            ) as r:
                if r.status_code != 200:
                    corps = b"".join(r.iter_bytes()).decode(errors="replace")
                    sys.exit(f"❌ HTTP {r.status_code} : {corps[:500]}")

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
    except httpx.TimeoutException:
        sys.exit("\n❌ Timeout. Le modèle ne répond pas.")

    duree = time.time() - t0
    out = usage.get("completion_tokens", 0)

    print(f"\n\n✅ Terminé en {duree:.1f}s — {morceaux} morceaux reçus")
    print(f"   usage : {json.dumps(usage, ensure_ascii=False)}")

    if out and duree:
        vitesse = out / duree
        print(f"   vitesse ≈ {vitesse:.0f} tokens/s")
        print(f"\n   → Extrapolation sur ton plan de gestion (~20 000 tokens de sortie "
              f"avec effort={args.effort}) :")
        print(f"      environ {20000 / vitesse / 60:.1f} minute(s). "
              f"Si c'est long, c'est normal.")


if __name__ == "__main__":
    main()

    