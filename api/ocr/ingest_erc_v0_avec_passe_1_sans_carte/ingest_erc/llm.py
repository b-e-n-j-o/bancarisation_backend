"""Appels LLM de la passe 1 (API Mistral). Désactivés si aucune clé :
le pipeline tourne alors en mode 100 % déterministe.

Deux appels seulement :
  - choisir_sections : routeur, ne reçoit que le PLAN du document (titres + signaux)
  - extraire_referentiel : reçoit les sections retenues, renvoie un JSON de faits

Modèle par défaut : GLM 5.2 (`zai-glm-5-2`) avec reasoning_effort.
Chez Mistral : uniquement `reasoning_effort` (jamais le champ Z.ai `thinking`).
"""
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel

from . import config


def actif() -> bool:
    return config.actif()


# ------------------------------------------------------------------ schémas
class ActionLLM(BaseModel):
    code: str
    intitule: str
    ugs: list[str]
    parcelles: list[str] = []
    cible: Optional[str] = None
    page: int


class UGLLM(BaseModel):
    code: str
    libelle: Optional[str] = None
    type_erc: Optional[str] = None
    page: int


class ReferentielLLM(BaseModel):
    familles_codes: dict[str, str]          # {"TU": "Travaux uniques", ...}
    actions: list[ActionLLM]
    ugs: list[UGLLM]
    annee_etat_zero: Optional[int] = None
    annee_N: Optional[int] = None
    annee_fin: Optional[int] = None
    remarques: list[str] = []


PROMPT_ROUTEUR = """Tu reçois le PLAN d'un plan de gestion de mesures compensatoires
(titres de sections, pages, signaux calculés). Choisis les sections qui décrivent :
le programme d'actions (codes d'actions), les unités de gestion, les parcelles
concernées par action, et les conventions temporelles (état zéro, année N).
Réponds en JSON : {"sections": [id, ...], "raison": "..."}. Maximum 20 sections."""

PROMPT_REFERENTIEL = """Tu extrais le RÉFÉRENTIEL d'un plan de gestion de compensation
écologique à partir d'extraits. Uniquement ce qui est écrit, rien d'inventé.
- actions : code normalisé sans espace (TU1), intitulé, UG concernées (UG1…),
  parcelles citées telles qu'écrites, espèce/cortège cible, page source.
- ugs : code, libellé, type ERC (E évitement / R / C compensation / A) si explicite.
- années : état zéro, année N, année de fin si écrites.
- remarques : toute contradiction ou coquille repérée (ex. deux fenêtres différentes).
Réponds uniquement avec le JSON conforme au schéma fourni."""


def _palier_refuse(corps: str) -> bool:
    c = corps.lower()
    return "tier_not_allowed" in c or "not available in your subscription" in c


def _effort_refuse(corps: str) -> bool:
    c = corps.lower()
    return "reasoning_effort" in c and any(
        k in c for k in ("not supported", "extra_forbidden", "invalid", "not permitted")
    )


def _texte_contenu(content) -> str:
    """Extrait le JSON utile, en ignorant les chunks de réflexion GLM/Magistral."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for chunk in content:
        if isinstance(chunk, str):
            parts.append(chunk)
            continue
        if isinstance(chunk, dict):
            ctype = chunk.get("type")
            if ctype in ("thinking", "think"):
                continue
            if ctype == "text":
                parts.append(chunk.get("text") or "")
            elif "text" in chunk:
                parts.append(chunk.get("text") or "")
            continue
        ctype = getattr(chunk, "type", None)
        if ctype in ("thinking", "think"):
            continue
        if ctype == "text" or hasattr(chunk, "text"):
            parts.append(getattr(chunk, "text", "") or "")
    return "".join(parts)


def extraire_json(texte: str) -> str:
    t = texte.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
        t = t.strip().removeprefix("json").strip()
    d, f = t.find("{"), t.rfind("}")
    return t[d:f + 1] if d != -1 and f > d else t


def _complete(client, *, model: str, messages: list, effort: str, max_tokens: int):
    """chat.complete avec reasoning_effort, repli si le modèle / SDK le refuse."""
    kwargs = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    effort_envoye = config.effort_api(effort) if effort and effort != "none" else None
    if effort_envoye:
        kwargs["reasoning_effort"] = effort_envoye
    try:
        return client.chat.complete(**kwargs)
    except TypeError:
        kwargs.pop("reasoning_effort", None)
        return client.chat.complete(**kwargs)
    except Exception as err:
        if effort_envoye and _effort_refuse(str(err)):
            print(f"   ⚠️  reasoning_effort={effort_envoye} refusé par {model}, "
                  "nouvel essai sans.", flush=True)
            kwargs.pop("reasoning_effort", None)
            return client.chat.complete(**kwargs)
        raise


def _appel(system: str, user: str, schema: type[BaseModel], *,
           effort: str | None = None, max_tokens: int | None = None) -> BaseModel:
    from mistralai import Mistral

    model = config.modele_llm()
    cle, src = config.api_key_llm(model)
    if not cle:
        raise RuntimeError(
            "LLM demandé mais aucune clé (MISTRAL_API_KEY ou MISTRAL_API_KEY_BEN)."
        )
    effort = effort or config.effort_llm()
    max_tokens = max_tokens if max_tokens is not None else config.max_tokens()
    print(f"🤖 [LLM] {model} · effort={config.effort_api(effort)} · clé={src} "
          f"· {schema.__name__}", flush=True)

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    def tenter(api_key: str, modele: str):
        return _complete(
            Mistral(api_key=api_key),
            model=modele,
            messages=messages,
            effort=effort,
            max_tokens=max_tokens,
        )

    try:
        resp = tenter(cle, model)
    except Exception as err:
        msg = str(err)
        if _palier_refuse(msg) and config.famille_llm(model) == "glm":
            repli = "mistral-medium-3-5"
            cle_repli, src_repli = config.api_key_llm(repli)
            print(f"   ⚠️  {model} hors palier ({src}). "
                  f"Repli {repli} · {src_repli}.", flush=True)
            if not cle_repli:
                raise
            resp = tenter(cle_repli, repli)
        else:
            raise
    txt = extraire_json(_texte_contenu(resp.choices[0].message.content))
    return schema.model_validate_json(txt)


def choisir_sections(plan: list[dict]) -> list[int]:
    class R(BaseModel):
        sections: list[int]
        raison: str = ""
    return _appel(
        PROMPT_ROUTEUR,
        json.dumps(plan, ensure_ascii=False),
        R,
        effort=config.effort_routeur(),
        max_tokens=2000,
    ).sections


def extraire_referentiel(extraits: list[dict], contexte: dict) -> ReferentielLLM:
    user = json.dumps({"contexte": contexte, "extraits": extraits,
                       "schema": ReferentielLLM.model_json_schema()}, ensure_ascii=False)
    return _appel(PROMPT_REFERENTIEL, user, ReferentielLLM)
