"""Appels LLM de la passe 1 (API Mistral). Désactivés si aucune clé :
le pipeline tourne alors en mode 100 % déterministe.

Trois appels :
  - cartographier : structure du plan de gestion (vocabulaire, fiches, tableaux)
  - choisir_sections : routeur, seulement si la carte n'a pas suffi
  - extraire_referentiel : extraits retenus → faits (si pas de carte)

Modèles : `config.ROLES` (défaut GLM 5.2 / `zai-glm-5-2`).
Chez Mistral : uniquement `reasoning_effort` (jamais le champ Z.ai `thinking`).
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from pydantic import BaseModel, ValidationError

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


def _sans_nulls(obj):
    """GLM envoie `null` là où le schéma attend un objet / une liste."""
    if isinstance(obj, dict):
        return {k: _sans_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_sans_nulls(x) for x in obj if x is not None]
    return obj


def _est_429(err) -> bool:
    s = str(err).lower()
    return "429" in s or "rate_limit" in s or "rate limit" in s


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
           effort: str | None = None, max_tokens: int | None = None,
           modele: str | None = None) -> BaseModel:
    from mistralai import Mistral

    model = modele or config.modele_llm()
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
        if _est_429(err):
            for i in range(2):
                attente = 20 * (i + 1)
                print(f"   ⚠️  rate limit, nouvel essai dans {attente}s…", flush=True)
                time.sleep(attente)
                try:
                    resp = tenter(cle, model)
                    break
                except Exception as err2:
                    err = err2
                    if not _est_429(err2) or i == 1:
                        raise
            else:
                raise
        elif _palier_refuse(msg) and config.famille_llm(model) == "glm":
            repli = config.modele_repli()
            cle_repli, src_repli = config.api_key_llm(repli)
            print(f"   ⚠️  {model} hors palier ({src}). "
                  f"Repli {repli} · {src_repli}.", flush=True)
            if not cle_repli:
                raise
            resp = tenter(cle_repli, repli)
        else:
            raise
    txt = extraire_json(_texte_contenu(resp.choices[0].message.content))
    try:
        data = _sans_nulls(json.loads(txt))
    except json.JSONDecodeError:
        return schema.model_validate_json(txt)
    try:
        return schema.model_validate(data)
    except ValidationError as err:
        print(f"   ⚠️  JSON {schema.__name__} non conforme ({err.error_count()} erreur(s))",
              flush=True)
        raise


def choisir_sections(plan: list[dict]) -> list[int]:
    class R(BaseModel):
        sections: list[int]
        raison: str = ""
    return _appel(
        PROMPT_ROUTEUR,
        json.dumps(plan, ensure_ascii=False),
        R,
        modele=config.modele_pour("routeur"),
        effort=config.effort_pour("routeur"),
        max_tokens=config.max_tokens_pour("routeur"),
    ).sections


def extraire_referentiel(extraits: list[dict], contexte: dict) -> ReferentielLLM:
    user = json.dumps({"contexte": contexte, "extraits": extraits,
                       "schema": ReferentielLLM.model_json_schema()}, ensure_ascii=False)
    return _appel(
        PROMPT_REFERENTIEL, user, ReferentielLLM,
        modele=config.modele_pour("referentiel"),
        effort=config.effort_pour("referentiel"),
        max_tokens=config.max_tokens_pour("referentiel"),
    )


# ================================================================== carte du document
PROMPT_CARTE = """Tu es l'analyste documentaire d'une plateforme de suivi des mesures
compensatoires (séquence ERC). Tu reçois un PLAN DE GESTION complet, page par page
(balises « === page N === » ; les grosses matrices sont tronquées).
Tu ne dois PAS extraire le contenu métier : tu décris la STRUCTURE du document pour qu'un
programme puisse ensuite l'exploiter mécaniquement.

Renvoie :
- vocabulaire : comment le document nomme ses unités spatiales de gestion (terme + préfixes des
  identifiants tels qu'écrits : « UG », « S », « Secteur », « Entité »…) et ses actions
  (forme codée ou libellés ; familles de codes avec préfixe exact et libellé, et une citation).
- fiches : la liste COMPLÈTE des fiches actions (code tel qu'écrit, titre tel qu'écrit, page
  où commence la fiche).
- champs_fiche : les libellés EXACTS utilisés dans les fiches pour les unités concernées, les
  parcelles, l'objectif, la période/calendrier, et le(s) libellé(s) à partir duquel commence le
  texte descriptif (fin de l'en-tête).
- tableaux : chaque tableau utile avec son rôle (synthese_actions = lien action↔unité ;
  synthese_mesures ; parcelles ; planning ; couts ; frise = ligne d'années + ligne N/N+x),
  sa page, son titre, et pour chaque rôle de colonne le libellé EXACT de l'en-tête. Indique les
  pages de suite d'un tableau coupé.
- sections : les grandes parties avec leur rôle et leurs pages.
- conventions : année de l'état zéro, année N, année de fin, montants HT/TTC si écrit, avec
  des citations.
- annexes : titre, pages, type.
- remarques : ce que tu remarques d'anormal dans le dossier. Pour chacune : une phrase
  citant les valeurs en cause, la page, un extrait verbatim qui la prouve, et son type :
  « valeur_du_projet » si elle contredit une valeur du projet (numéro d'arrêté, année de début
  ou de fin, durée, HT/TTC), « incoherence_document » si c'est une erreur du dossier sans effet
  sur ces valeurs (renvoi erroné, objectif qui ne correspond pas au tableau de synthèse),
  « qualite_de_lecture » si c'est un défaut de conversion ou d'OCR de notre côté.

Règles impératives :
- Chaque libellé, titre, préfixe et citation doit être COPIÉ du document, pas reformulé.
- Les citations font 5 à 15 mots et la page doit être exacte : elles seront vérifiées
  automatiquement, et tout élément non retrouvé sera ignoré.
- N'invente rien. Laisse vide ce qui n'existe pas.
Réponds uniquement avec un JSON conforme au schéma fourni."""


def _paquets(texte: str, budget: int) -> list[str]:
    pages = re.split(r"(?=^=== page \d+ ===$)", texte, flags=re.M)
    lots, cur, prev = [], "", ""
    for p in pages:
        if cur and (len(cur) + len(p)) // 3 > budget:
            lots.append(cur)
            cur = prev[-2000:]   # léger recouvrement (fin de page précédente)
        cur += p
        if p.strip():
            prev = p
    if cur:
        lots.append(cur)
    return lots


def cartographier(texte: str):  # pragma: no cover - réseau
    from .carte import CarteDocument, estimer_tokens, fusionner
    schema = json.dumps(CarteDocument.model_json_schema(), ensure_ascii=False)
    budget = config.budget_carte()
    lots = [texte] if estimer_tokens(texte) <= budget else _paquets(texte, budget // 2)
    cartes = []
    for i, lot in enumerate(lots, 1):
        entete = "" if len(lots) == 1 else f"(partie {i}/{len(lots)} du document ; ne décris que ce que tu vois)\n"
        cartes.append(_appel(
            PROMPT_CARTE + "\n\nSchéma JSON :\n" + schema,
            entete + lot,
            CarteDocument,
            modele=config.modele_pour("carte"),
            effort=config.effort_pour("carte"),
            max_tokens=config.max_tokens_pour("carte"),
        ))
    return fusionner(cartes)
