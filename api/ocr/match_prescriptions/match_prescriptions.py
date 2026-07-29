"""
match_prescriptions.py — Appariement LLM prescriptions (arrêté) ↔ échéances (plan de gestion BE).

Le LLM propose les liens de couverture n:n. Ils sont insérés en mode='ia' (avec
confiance + justification) et restent des PROPOSITIONS tant qu'un humain ne les a
pas validés (bascule en mode='user'). Aucun pré-match déterministe : tout passe
par le modèle. Réflexion "low" — la tâche est un appariement sémantique, pas une
extraction.

Usage (esquisse) :
    from api.ocr.match_prescriptions.match_prescriptions import match_prescriptions, to_couverture_rows
    liens, usage = match_prescriptions(prescriptions, echeances)
    # puis crud.inserer_couvertures_ia / service.lancer_appariement
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from pydantic import BaseModel, Field, ValidationError
from mistralai import Mistral

logger = logging.getLogger("match_prescriptions")

MODEL = os.getenv("MISTRAL_MODEL_MATCH", "mistral-medium-3-5")
REASONING_EFFORT = os.getenv("MISTRAL_REASONING_EFFORT_MATCH", "low")  # réflexion faible
PRIX_INPUT_PAR_M = float(os.getenv("MISTRAL_PRIX_INPUT_M", "0.40"))
PRIX_OUTPUT_PAR_M = float(os.getenv("MISTRAL_PRIX_OUTPUT_M", "2.00"))


# ---------------------------------------------------------------------------
# Schéma de sortie
# ---------------------------------------------------------------------------

class LienPropose(BaseModel):
    prescription_id: str
    echeance_id: str
    confiance: float = Field(ge=0.0, le=1.0)
    justification: str = ""   # une phrase, pour l'écran d'appariement


class Appariement(BaseModel):
    liens: list[LienPropose] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Projections compactes envoyées au modèle (on ne donne que l'utile + l'id)
# ---------------------------------------------------------------------------

def _presc_min(p: dict) -> dict:
    return {
        "id": p["id"],
        "article": p.get("article"),
        "intitule": p.get("intitule"),
        "categorie": p.get("categorie"),
        "nature": p.get("nature"),
        "cible": _cible(p),
    }


def _cible(p: dict) -> Optional[str]:
    if p.get("cible_valeur") is None:
        return None
    return f"{p['cible_valeur']} {p.get('cible_unite') or ''}".strip()


def _ech_min(e: dict) -> dict:
    return {
        "id": e["id"],
        "code_operation": e.get("code_operation"),
        "type_operation": e.get("type_operation"),
        "type_metier": e.get("type_metier"),
        "libelle": e.get("libelle"),
        "objectif_operationnel": e.get("objectif_operationnel"),
        "lib_thema": e.get("lib_thema"),
        "recurrence": e.get("recurrence"),
    }


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Tu rapproches les OBLIGATIONS d'un arrêté préfectoral (prescriptions) des ACTIONS \
planifiées par le bureau d'études dans son plan de gestion (échéances). Ton but : \
dire quelle(s) échéance(s) METTENT EN ŒUVRE chaque prescription.

On te donne deux listes JSON, chaque élément portant un "id". Tu renvoies \
UNIQUEMENT un objet JSON {"liens": [ ... ]} où chaque lien est :
{"prescription_id": "<id>", "echeance_id": "<id>", "confiance": 0.0..1.0, \
"justification": "<une phrase>"}.

Règles impératives :
- Relation N:N : une prescription peut être couverte par plusieurs échéances, et \
une échéance peut couvrir plusieurs prescriptions.
- NE FORCE JAMAIS un lien. Si aucune échéance ne correspond vraiment à une \
prescription, ne renvoie rien pour elle : une obligation non couverte est une \
information légitime et importante (ce n'est pas une erreur à combler).
- N'utilise QUE les id fournis, à l'identique. N'invente jamais d'id.
- Fonde-toi sur le SENS (intitulé, catégorie, code et type d'opération, objectif, \
récurrence), pas sur l'ordre des listes.
- Calibre la confiance : proche de 1 quand le rapprochement est évident \
(ex. « fauche annuelle » ↔ une échéance d'entretien par fauche), plus basse en cas \
de correspondance partielle ou incertaine.
- justification : une phrase courte qui explique le rapprochement, destinée au \
valideur humain.

Ne renvoie aucun texte hors du JSON.
"""


def build_user_prompt(prescriptions: list[dict], echeances: list[dict]) -> str:
    return (
        "PRESCRIPTIONS (obligations de l'arrêté) :\n"
        + json.dumps([_presc_min(p) for p in prescriptions], ensure_ascii=False, indent=1)
        + "\n\nÉCHÉANCES (actions planifiées du BE) :\n"
        + json.dumps([_ech_min(e) for e in echeances], ensure_ascii=False, indent=1)
        + "\n\nRenvoie les liens pertinents au format demandé."
    )


# ---------------------------------------------------------------------------
# Appel LLM
# ---------------------------------------------------------------------------

def _parse_json(raw: str) -> dict:
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1]
        if txt.lstrip().startswith("json"):
            txt = txt.lstrip()[4:]
    start, end = txt.find("{"), txt.rfind("}")
    if start != -1 and end != -1:
        txt = txt[start : end + 1]
    return json.loads(txt)


def match_prescriptions(
    prescriptions: list[dict],
    echeances: list[dict],
    client: Optional[Mistral] = None,
    model: str = MODEL,
    max_retries: int = 2,
) -> tuple[list[LienPropose], dict]:
    """Propose les liens prescription↔échéance. Renvoie (liens_valides, usage)."""
    if not prescriptions or not echeances:
        return [], {"note": "liste vide, aucun appariement", "input_tokens": 0, "output_tokens": 0}

    client = client or Mistral(api_key=os.environ["MISTRAL_API_KEY"])
    valid_p = {p["id"] for p in prescriptions}
    valid_e = {e["id"] for e in echeances}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(prescriptions, echeances)},
    ]

    last_err: Optional[Exception] = None
    for tentative in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            resp = client.chat.complete(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
                reasoning_effort=REASONING_EFFORT,   # "low"
            )
        except TypeError:
            resp = client.chat.complete(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
            )
        dt = time.perf_counter() - t0

        raw = resp.choices[0].message.content
        u = resp.usage
        usage = {
            "modele": model,
            "tentative": tentative,
            "input_tokens": getattr(u, "prompt_tokens", None),
            "output_tokens": getattr(u, "completion_tokens", None),
            "total_tokens": getattr(u, "total_tokens", None),
            "duree_s": round(dt, 2),
        }
        usage["cout_estime_usd"] = _cout(usage)

        try:
            data = _parse_json(raw)
            appariement = Appariement.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            logger.warning("parse/validation KO (tentative %d) : %s", tentative, e)
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": f"JSON invalide ({e}). Renvoie UNIQUEMENT l'objet JSON corrigé.",
            })
            continue

        # Garde-fou : ne garder que les liens dont les DEUX ids existent réellement.
        retenus, rejetes = [], 0
        vus: set[tuple[str, str]] = set()
        for lien in appariement.liens:
            paire = (lien.prescription_id, lien.echeance_id)
            if lien.prescription_id in valid_p and lien.echeance_id in valid_e and paire not in vus:
                retenus.append(lien)
                vus.add(paire)
            else:
                rejetes += 1
        usage["liens_proposes"] = len(appariement.liens)
        usage["liens_retenus"] = len(retenus)
        usage["liens_rejetes_ids_invalides"] = rejetes
        logger.info("appariement | %s", usage)
        return retenus, usage

    raise RuntimeError(f"Appariement impossible après {max_retries} tentatives : {last_err}")


def _cout(usage: dict) -> Optional[float]:
    it, ot = usage.get("input_tokens"), usage.get("output_tokens")
    if it is None or ot is None:
        return None
    return round(it / 1_000_000 * PRIX_INPUT_PAR_M + ot / 1_000_000 * PRIX_OUTPUT_PAR_M, 4)


# ---------------------------------------------------------------------------
# Mapping vers la table prescription_couverture
# ---------------------------------------------------------------------------

def to_couverture_rows(liens: list[LienPropose]) -> list[dict]:
    """Rows prêts pour l'insertion. À insérer en ON CONFLICT DO NOTHING pour ne
    JAMAIS écraser un lien déjà validé par un humain (mode='user')."""
    return [
        {
            "prescription_id": l.prescription_id,
            "echeance_id": l.echeance_id,
            "mode": "ia",
            "confiance": l.confiance,
            "note": l.justification,
        }
        for l in liens
    ]