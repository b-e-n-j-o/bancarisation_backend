"""
mistral_client.py — Client HTTP Mistral partagé (streaming + logging).

Utilisé par extract_dossier_mistral, extract_actions_mistral, extract_echeances_mistral.

Le streaming ne change ni le coût (mêmes tokens facturés) ni la qualité de sortie :
c'est uniquement le mode de transport (chunks SSE vs réponse monolithique).
Léger surcoût réseau négligeable ; bénéfice = suivi temps réel pendant les longues extractions.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

API_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MODEL = "mistral-medium-3-5"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TOKENS = 32000
MAX_TENTATIVES = 3

PRIX = {
    "mistral-medium-3-5": (1.50, 7.50),
    "mistral-small-latest": (0.15, 0.60),
    "mistral-large-latest": (0.50, 1.50),
}
PRIX_DEFAUT = PRIX[DEFAULT_MODEL]

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger("mistral")


# --- Schéma Mistral ---------------------------------------------------------

def _inliner(noeud, defs: dict):
    if isinstance(noeud, dict):
        if "$ref" in noeud:
            nom = noeud["$ref"].split("/")[-1]
            return _inliner(defs[nom], defs)
        return {k: _inliner(v, defs) for k, v in noeud.items() if k != "$defs"}
    if isinstance(noeud, list):
        return [_inliner(v, defs) for v in noeud]
    return noeud


def _durcir(noeud):
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


def schema_mistral(modele: type[BaseModel]) -> dict:
    brut = modele.model_json_schema()
    plat = _inliner(brut, brut.get("$defs", {}))
    return _durcir(plat)


# --- Parsing ----------------------------------------------------------------

def texte_des_chunks(valeur) -> tuple[str, str]:
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


def extraire_json(texte: str) -> str:
    t = texte.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
        t = t.strip().removeprefix("json").strip()
    d, f = t.find("{"), t.rfind("}")
    return t[d:f + 1] if d != -1 and f > d else t


def dump_debug(
    debug_dir: Path,
    prefixe: str,
    tentative: int,
    reflexion: str,
    reponse: str,
    err: Exception | None,
) -> Path:
    debug_dir.mkdir(parents=True, exist_ok=True)
    if reflexion:
        (debug_dir / f"{prefixe}_t{tentative}_reflexion.txt").write_text(
            reflexion, encoding="utf-8"
        )
    cible = debug_dir / f"{prefixe}_t{tentative}_reponse_brute.txt"
    cible.write_text(reponse, encoding="utf-8")
    if err:
        (debug_dir / f"{prefixe}_t{tentative}_erreur.txt").write_text(str(err), encoding="utf-8")
    return cible


# --- Compteur ---------------------------------------------------------------

class Compteur:
    def __init__(self, model: str, prix_in: float, prix_out: float):
        self.model = model
        self.prix_in = prix_in
        self.prix_out = prix_out
        self.appels = 0
        self.tok_in = 0
        self.tok_out = 0
        self.car_reflexion = 0
        self.car_reponse = 0
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
            "",
            "─" * 62,
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
        ])


# --- Streaming --------------------------------------------------------------

def appel_streame(
    client: httpx.Client,
    api_key: str,
    payload: dict,
    *,
    etiquettes: str = "",
) -> tuple[str, str, dict, str | None]:
    """Retourne (reflexion, reponse, usage, finish_reason)."""
    reflexion: list[str] = []
    reponse: list[str] = []
    usage: dict = {}
    finish: str | None = None
    t0 = time.time()
    premier: float | None = None
    en_reflexion = False
    dernier_log = 0.0

    with client.stream(
        "POST",
        API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
    ) as r:
        if r.status_code != 200:
            corps = b"".join(r.iter_bytes()).decode(errors="replace")
            raise RuntimeError(f"HTTP {r.status_code} : {corps[:800]}")

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
                log.info("%s premier token après %.1fs", etiquettes, premier)
                print(f"   ⏱️  Premier token après {premier:.1f}s")

            d_refl, d_rep = texte_des_chunks(delta)
            if d_refl:
                if not en_reflexion:
                    log.info("%s réflexion démarrée", etiquettes)
                    print("   🧠 réflexion en cours …")
                    en_reflexion = True
                reflexion.append(d_refl)
            if d_rep:
                if en_reflexion:
                    log.info(
                        "%s réflexion terminée (%d car.)",
                        etiquettes,
                        len("".join(reflexion)),
                    )
                    print(
                        f"\n   ✍️  réflexion terminée ({len(''.join(reflexion)):,} car.), "
                        "rédaction du JSON …"
                    )
                    en_reflexion = False
                reponse.append(d_rep)

            maintenant = time.time()
            if maintenant - dernier_log > 2.0:
                dernier_log = maintenant
                ecoule = maintenant - t0
                car = len("".join(reflexion)) + len("".join(reponse))
                phase = "réflexion" if en_reflexion else "JSON"
                print(
                    f"\r   {ecoule:5.0f}s · {car:>7,} car. · "
                    f"{car/max(ecoule,1):>5.0f} car/s · {phase}    ",
                    end="",
                    flush=True,
                )

    duree = time.time() - t0
    log.info(
        "%s terminé en %.0fs · in=%s out=%s finish=%s",
        etiquettes,
        duree,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        finish,
    )
    print(f"\r   ✅ {duree:.0f}s au total" + " " * 45)
    return "".join(reflexion), "".join(reponse), usage, finish


# --- Extraction générique ---------------------------------------------------

def extraire_structure(
    *,
    system_prompt: str,
    user_prompt: str,
    result_type: type[T],
    etiquettes: str,
    debug_dir: Path,
    debug_prefixe: str,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    utiliser_schema: bool = False,
    compteur: Compteur | None = None,
    schema_name: str = "extraction",
) -> T:
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        sys.exit("❌ MISTRAL_API_KEY absente.")

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    derniere_erreur: Exception | None = None

    with httpx.Client(timeout=httpx.Timeout(30.0, read=1800.0)) as client:
        for tentative in range(1, MAX_TENTATIVES + 1):
            log.info(
                "%s tentative %d/%d · model=%s effort=%s schema=%s",
                etiquettes, tentative, MAX_TENTATIVES, model, effort, utiliser_schema,
            )
            print(
                f"\n🤖 [{etiquettes}] {model} · effort={effort} · schema={utiliser_schema} "
                f"· tentative {tentative}/{MAX_TENTATIVES}"
            )

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
                        "name": schema_name,
                        "schema": schema_mistral(result_type),
                        "strict": True,
                    },
                }

            reflexion, reponse, usage, finish = appel_streame(
                client, api_key, payload, etiquettes=etiquettes,
            )

            if compteur:
                compteur.ajouter(usage, reflexion, reponse)

            print(
                f"   in={usage.get('prompt_tokens', 0):,} "
                f"out={usage.get('completion_tokens', 0):,} "
                f"· finish_reason={finish} · réponse={len(reponse):,} car."
            )

            if finish == "length":
                log.warning("%s tronqué à max_tokens=%d", etiquettes, max_tokens)
                print(
                    f"   🔪 TRONQUÉ : max_tokens={max_tokens:,}. "
                    f"Relance avec --max-tokens {max_tokens * 2}."
                )

            try:
                resultat = result_type.model_validate_json(extraire_json(reponse))
                dump_debug(debug_dir, debug_prefixe, tentative, reflexion, reponse, None)
                log.info("%s validation OK", etiquettes)
                return resultat
            except (ValidationError, json.JSONDecodeError) as err:
                derniere_erreur = err
                chemin = dump_debug(debug_dir, debug_prefixe, tentative, reflexion, reponse, err)
                log.error("%s validation échouée : %s", etiquettes, err)
                print(f"\n   ⚠️  VALIDATION ÉCHOUÉE — dump → {chemin}")
                print("   ┌─ erreur " + "─" * 48)
                for ligne in str(err)[:500].splitlines():
                    print(f"   │ {ligne}")
                print("   └" + "─" * 58)

                if tentative < MAX_TENTATIVES:
                    print("   ↻ réinjection de l'erreur au modèle …")
                    messages += [
                        {"role": "assistant", "content": reponse},
                        {"role": "user", "content": (
                            "Ta réponse n'est pas conforme au schéma. Erreur :\n"
                            f"{err}\n\nRenvoie UNIQUEMENT le JSON corrigé, complet, "
                            "sans commentaire ni balise markdown."
                        )},
                    ]

    raise SystemExit(
        f"\n❌ [{etiquettes}] invalide après {MAX_TENTATIVES} tentatives.\n"
        f"   Dernière erreur : {derniere_erreur}\n"
        f"   Dumps : {debug_dir}/"
    )


def configurer_logging(log_dir: Path, run_id: str) -> Path:
    """Console INFO + fichier détaillé. Retourne le chemin du fichier log."""
    log_dir.mkdir(parents=True, exist_ok=True)
    fichier = log_dir / f"pipeline_{run_id}.log"

    racine = logging.getLogger("pipeline")
    racine.setLevel(logging.DEBUG)
    racine.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    fh = logging.FileHandler(fichier, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))

    racine.addHandler(fh)
    racine.addHandler(ch)
    logging.getLogger("mistral").setLevel(logging.DEBUG)

    return fichier
