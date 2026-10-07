"""Appels LLM de la passe 1 (API Mistral). Désactivés si aucune clé :
le pipeline tourne alors en mode 100 % déterministe.

Trois appels :
  - cartographier : structure du plan de gestion (vocabulaire, fiches, tableaux)
  - choisir_sections : routeur, seulement si la carte n'a pas suffi
  - extraire_referentiel : extraits retenus → faits (si pas de carte)

Modèles : `config.ROLES` (défaut GLM 5.2 / `zai-glm-5-2`).
GLM 5.2 : `reasoning_effort="none"` (pas de bloc de réflexion).
Autres modèles Mistral : `reasoning_effort` via le SDK.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Optional

from pydantic import BaseModel, ValidationError

from . import config

_local = threading.local()


def _compteur() -> dict:
    if not getattr(_local, "tokens", None):
        _local.tokens = {"n": 0, "prompt": 0, "completion": 0, "total": 0}
    return _local.tokens


def reset_compteur_tokens() -> None:
    _local.tokens = {"n": 0, "prompt": 0, "completion": 0, "total": 0}


def snapshot_tokens() -> dict:
    return dict(_compteur())


def _usage_de(resp) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {"prompt": 0, "completion": 0, "total": 0}
    if isinstance(u, dict):
        prompt = int(u.get("prompt_tokens") or 0)
        completion = int(u.get("completion_tokens") or 0)
        total = int(u.get("total_tokens") or 0)
    else:
        prompt = int(getattr(u, "prompt_tokens", 0) or 0)
        completion = int(getattr(u, "completion_tokens", 0) or 0)
        total = int(getattr(u, "total_tokens", 0) or 0)
    if not total:
        total = prompt + completion
    return {"prompt": prompt, "completion": completion, "total": total}


def _noter_tokens(schema: str, usage: dict) -> None:
    c = _compteur()
    c["n"] += 1
    c["prompt"] += usage["prompt"]
    c["completion"] += usage["completion"]
    c["total"] += usage["total"]
    print(
        f"   tokens : in={usage['prompt']:,} out={usage['completion']:,} "
        f"total={usage['total']:,}  · {schema}  · cumul={c['total']:,} ({c['n']} appel{'s' if c['n'] > 1 else ''})",
        flush=True,
    )


def bilan_tokens(libelle: str = "pipeline") -> dict:
    c = snapshot_tokens()
    print(
        f"📊 tokens LLM {libelle} : {c['n']} appel(s) · "
        f"in={c['prompt']:,} out={c['completion']:,} total={c['total']:,}",
        flush=True,
    )
    return c


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


def _enveloppe(data: dict):
    """Réponse chat.completions → objet compatible SDK (`choices`, `usage`)."""
    from types import SimpleNamespace
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    return SimpleNamespace(
        usage=data.get("usage") or {},
        choices=[SimpleNamespace(
            finish_reason=ch.get("finish_reason"),
            message=SimpleNamespace(content=msg.get("content")),
        )],
    )


def _http_complete(api_key: str, payload: dict):
    """Même payload que le SDK si `reasoning_effort` n'est pas dans la signature."""
    import httpx
    r = httpx.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=300.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:800]}")
    return _enveloppe(r.json())


def _complete(api_key: str, *, model: str, messages: list, effort: str, max_tokens: int):
    from mistralai import Mistral

    kwargs = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    if config.famille_llm(model) == "glm":
        kwargs["reasoning_effort"] = "none"
    elif effort:
        kwargs["reasoning_effort"] = config.effort_api(effort)

    try:
        return Mistral(api_key=api_key).chat.complete(**kwargs)
    except TypeError:
        return _http_complete(api_key, kwargs)
    except Exception as err:
        effort_envoye = kwargs.get("reasoning_effort")
        if effort_envoye and _effort_refuse(str(err)):
            print(f"   ⚠️  reasoning_effort={effort_envoye} refusé par {model}, "
                  "nouvel essai sans.", flush=True)
            kwargs.pop("reasoning_effort", None)
            try:
                return Mistral(api_key=api_key).chat.complete(**kwargs)
            except TypeError:
                return _http_complete(api_key, kwargs)
        raise


def _appel(system: str, user: str, schema: type[BaseModel], *,
           effort: str | None = None, max_tokens: int | None = None,
           modele: str | None = None) -> BaseModel:
    model = modele or config.modele_llm()
    cle, src = config.api_key_llm(model)
    if not cle:
        raise RuntimeError(
            "LLM demandé mais aucune clé (MISTRAL_API_KEY ou MISTRAL_API_KEY_BEN)."
        )
    effort = effort or config.effort_llm()
    max_tokens = max_tokens if max_tokens is not None else config.max_tokens()
    extra = ("effort=none" if config.famille_llm(model) == "glm"
             else f"effort={config.effort_api(effort)}")
    print(f"🤖 [LLM] {model} · {extra} · clé={src} · {schema.__name__}", flush=True)

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    def tenter(api_key: str, modele: str):
        return _complete(
            api_key,
            model=modele,
            messages=messages,
            effort=effort,
            max_tokens=max_tokens,
        )

    t0 = time.time()

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
    dt = round(time.time() - t0, 1)
    print(f"   ⏱ {schema.__name__} : {dt}s", flush=True)
    _noter_tokens(schema.__name__, _usage_de(resp))
    contenu = resp.choices[0].message.content
    fin = getattr(resp.choices[0], "finish_reason", None)
    n_car = len(contenu) if isinstance(contenu, str) else len(str(contenu or ""))
    print(f"   stop={fin or '?'}  · max_tokens={max_tokens:,}  · réponse={n_car:,} car.",
          flush=True)
    txt = extraire_json(_texte_contenu(contenu))
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
