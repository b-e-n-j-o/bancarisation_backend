"""Client Mistral unique : chat JSON (texte), chat JSON (image), OCR. (identique à pgcal)

- Sortie structurée : `response_format=json_schema` dérivé du modèle Pydantic,
  repli sur `json_object` si l'API refuse le schéma, puis validation Pydantic
  et une tentative de réparation en cas d'erreur.
- Cache disque : chaque appel est rejouable sans coût (clé = hash de la requête).
- Journal : une ligne JSON par appel dans `llm_calls.jsonl` du run
  (durée, modèle, usage tokens). La clé API n'est jamais écrite.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings

logger = logging.getLogger("arx.llm")

T = TypeVar("T", bound=BaseModel)


def _texte_contenu(content) -> str:
    """Texte utile d'une réponse. mistral-large-4 préfixe un ThinkChunk à ignorer."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    morceaux = content if isinstance(content, list) else [content]
    visibles: list[str] = []
    reflexions: list[str] = []
    for chunk in morceaux:
        if isinstance(chunk, str):
            visibles.append(chunk)
            continue
        type_chunk = chunk.get("type") if isinstance(chunk, dict) else getattr(chunk, "type", None)
        if type_chunk in ("thinking", "think") or type(chunk).__name__ == "ThinkChunk":
            interne = chunk.get("thinking") if isinstance(chunk, dict) else getattr(chunk, "thinking", None)
            if interne:
                reflexions.append(_texte_contenu(interne))
            continue
        if isinstance(chunk, dict):
            visibles.append(chunk.get("text") or "")
        else:
            visibles.append(getattr(chunk, "text", None) or "")
    texte = "".join(visibles).strip()
    return texte or "".join(reflexions).strip()


class LLMUnavailable(RuntimeError):
    pass


def _masquer_secret(message: str) -> str:
    masque = re.sub(
        r"(api[_-]?key|bearer|authorization)\s*[:=]\s*\S+",
        r"\1=[masqué]",
        message,
        flags=re.IGNORECASE,
    )
    return masque[:400]


def usage_dict(usage: Any) -> dict[str, int]:
    """Normalise l'usage Mistral (objet SDK ou dict) en compteurs de tokens."""
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if hasattr(usage, "model_dump"):
        raw = usage.model_dump()
    elif isinstance(usage, dict):
        raw = usage
    else:
        raw = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
    def _n(*keys: str) -> int:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return 0
    prompt = _n("prompt_tokens", "input_tokens")
    completion = _n("completion_tokens", "output_tokens")
    total = _n("total_tokens") or (prompt + completion)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


class MistralClient:
    def __init__(self, settings: Settings, run_dir: Path, on_call: Callable[[dict], None] | None = None):
        self.s = settings
        self.cache_dir = settings.runs_dir / ".cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = run_dir / "llm_calls.jsonl"
        self._client = None
        self._journal: list[dict] = []
        self.on_call = on_call

    # ------------------------------------------------------------------ infra
    @property
    def client(self):
        if self.s.offline:
            raise LLMUnavailable("mode hors ligne : aucun appel API")
        if self._client is None:
            if not self.s.api_key:
                raise LLMUnavailable("MISTRAL_API_KEY absente")
            try:
                from mistralai.client import Mistral  # SDK >= 2
            except ImportError:  # SDK 1.x
                from mistralai import Mistral
            self._client = Mistral(api_key=self.s.api_key)
        return self._client

    def _key(self, payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _cache_get(self, key: str):
        p = self.cache_dir / f"{key}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def _cache_put(self, key: str, value) -> None:
        (self.cache_dir / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False))

    def _log(self, **kw) -> None:
        kw.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        self._journal.append(kw)
        with self.log_path.open("a") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")
        usage = kw.get("usage") or {}
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        logger.info(
            "arx %s modele=%s tag=%s cache=%s ok=%s duree=%ss tokens=%s",
            kw.get("kind", "appel"),
            kw.get("model"),
            kw.get("tag"),
            kw.get("cached"),
            kw.get("ok", True),
            kw.get("seconds"),
            tokens,
        )
        if self.on_call:
            try:
                self.on_call(self.bilan())
            except Exception:  # noqa: BLE001 — le journal ne doit pas casser l'analyse
                logger.exception("callback journal LLM ignorée")

    def bilan(self) -> dict:
        """Compte les allers-retours API (`kind=http`). Sans eux (client simulé),
        on retombe sur les lignes `requete`. Le cache est toujours compté à part."""
        http = [c for c in self._journal if c.get("kind") == "http" and c.get("ok") is not False]
        requetes = [c for c in self._journal if c.get("kind") == "requete"]
        cache = [c for c in requetes if c.get("cached")]
        api = http or [c for c in requetes if not c.get("cached") and c.get("ok") is not False]
        echecs = [c for c in self._journal if c.get("ok") is False]

        def _tokens(key: str) -> int:
            total = 0
            for call in api:
                usage = call.get("usage") or {}
                if isinstance(usage, dict):
                    total += int(usage.get(key) or 0)
            return total

        par_tag: dict[str, int] = dict(Counter(str(c.get("tag") or "?") for c in api))
        par_modele: dict[str, int] = dict(Counter(str(c.get("model") or "?") for c in api))
        return {
            "appels": len(api) + len(cache),
            "appels_api": len(api),
            "appels_cache": len(cache),
            "echecs": len(echecs),
            "tokens_prompt": _tokens("prompt_tokens"),
            "tokens_completion": _tokens("completion_tokens"),
            "tokens_total": _tokens("total_tokens"),
            "secondes": round(sum(float(c.get("seconds") or 0) for c in api), 1),
            "par_tag": par_tag,
            "par_modele": par_modele,
        }

    def _retry(self, fn, *, tag: str, model: str, tries: int = 4):
        for i in range(tries):
            t0 = time.time()
            try:
                return fn()
            except LLMUnavailable:
                raise
            except Exception as e:  # noqa: BLE001 — SDK lève des types variés
                msg = str(e)
                final = i == tries - 1 or any(c in msg for c in ("400", "401", "403", "422"))
                self._log(
                    kind="tentative", tag=tag, model=model, ok=False, attempt=i + 1,
                    seconds=round(time.time() - t0, 2), erreur=_masquer_secret(msg),
                )
                if final:
                    raise
                time.sleep(2 ** i * 2)
        raise RuntimeError("retry épuisé")  # pragma: no cover

    # ------------------------------------------------------------------ chat
    def chat_json(
        self,
        schema: type[T],
        system: str,
        user: str | list,
        *,
        tag: str,
        n: int = 1,
        temperature: float | None = None,
        cache_key_hint: str | None = None,
    ) -> list[T]:
        """Renvoie `n` objets validés (échantillons indépendants)."""
        temperature = self.s.temperature if temperature is None else temperature
        model = self.s.vision_model if isinstance(user, list) else self.s.llm_model
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        key = self._key({"m": model, "msg": messages, "schema": schema.__name__, "t": temperature, "n": n})
        cached = self._cache_get(key)
        if cached is not None:   # rejouable même hors ligne
            self._log(kind="requete", tag=tag, model=model, cached=True, n=n, ok=True, seconds=0)
            return [schema.model_validate(c) for c in cached]
        if self.s.offline:
            raise LLMUnavailable("mode hors ligne : aucun appel API")

        t0 = time.time()
        self._tag = tag
        raw_choices, usage = self._complete(model, messages, schema, temperature, n, cache_key_hint)
        out: list[T] = []
        for raw in raw_choices:
            obj = self._validate_or_repair(schema, raw, model, tag)
            if obj is not None:
                out.append(obj)
        self._log(kind="requete", tag=tag, model=model, cached=False, n=n, ok=len(out),
                  seconds=round(time.time() - t0, 2), usage=usage_dict(usage))
        self._cache_put(key, [o.model_dump() for o in out])
        return out

    def _complete(self, model, messages, schema, temperature, n, cache_key_hint):
        tag = getattr(self, "_tag", None) or schema.__name__
        rf_schema = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
        }
        kwargs = dict(model=model, messages=messages, temperature=temperature, max_tokens=16_000)
        if temperature is not None and temperature <= 0:
            kwargs["top_p"] = 1
        if cache_key_hint:
            kwargs["prompt_cache_key"] = cache_key_hint

        def call(rf, n_):
            params = dict(kwargs)
            modele = params.pop("model")
            return self._http_chat(tag=tag, model=modele, n=n_, response_format=rf, **params)

        try:
            resp, usage = self._retry(lambda: call(rf_schema, n), tag=tag, model=model)
        except LLMUnavailable:
            raise
        except Exception:  # schéma refusé ou `n` non supporté : repli
            resp, usage = None, {}
        if resp is None:
            rf_obj = {"type": "json_object"}
            choices: list = []
            total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            for i in range(n):
                r, u = self._retry(lambda: call(rf_obj, 1), tag=f"{tag}:repli", model=model)
                choices.append(_texte_contenu(r.choices[0].message.content))
                for k in total:
                    total[k] += int((u or {}).get(k) or 0)
            return choices, total
        return [_texte_contenu(c.message.content) for c in resp.choices], usage

    def _http_chat(self, *, tag: str, model: str, n: int, response_format, **kwargs):
        t0 = time.time()
        resp = self.client.chat.complete(model=model, response_format=response_format, n=n, **kwargs)
        usage = usage_dict(getattr(resp, "usage", None))
        self._log(
            kind="http", tag=tag, model=model, cached=False, ok=True, n=n,
            seconds=round(time.time() - t0, 2), usage=usage,
        )
        return resp, usage

    def _validate_or_repair(self, schema: type[T], raw, model: str, tag: str = "repair") -> T | None:
        text = _texte_contenu(raw)
        try:
            return schema.model_validate_json(text)
        except ValidationError as e:
            err = str(e)[:2000]
        repair_msgs = [
            {"role": "system", "content": "Tu corriges un JSON pour qu'il respecte un schéma. Réponds uniquement par le JSON corrigé."},
            {"role": "user", "content": f"Schéma :\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n\nErreurs :\n{err}\n\nJSON :\n{text}"},
        ]
        try:
            r, _usage = self._retry(
                lambda: self._http_chat(
                    tag=f"{tag}:repair", model=model, n=1,
                    response_format={"type": "json_object"},
                    messages=repair_msgs, temperature=0, top_p=1,
                ),
                tag=f"{tag}:repair", model=model,
            )
            return schema.model_validate_json(_texte_contenu(r.choices[0].message.content))
        except Exception:  # noqa: BLE001
            return None

    # ---------------------------------------------------------------- vision
    def vision_json(self, schema: type[T], system: str, prompt: str, png: bytes, *, tag: str) -> T | None:
        b64 = base64.b64encode(png).decode()
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": f"data:image/png;base64,{b64}"},
        ]
        res = self.chat_json(schema, system, content, tag=tag, n=1, temperature=0)
        return res[0] if res else None

    # ------------------------------------------------------------------- OCR
    def ocr_pages(self, pdf_path: Path, page_indices: list[int]) -> list[dict]:
        """OCR des pages demandées (0-based). Renvoie [{index, markdown, tables, confidence}]."""
        if self.s.offline:
            raise LLMUnavailable("mode hors ligne : aucun appel API")
        data = pdf_path.read_bytes()
        key = self._key({"ocr": self.s.ocr_model, "sha": hashlib.sha256(data).hexdigest(), "pages": page_indices})
        cached = self._cache_get(key)
        if cached is not None:
            self._log(kind="requete", tag="ocr", model=self.s.ocr_model, cached=True, ok=True,
                      pages=len(page_indices), seconds=0)
            return cached
        doc = {"type": "document_url", "document_url": "data:application/pdf;base64," + base64.b64encode(data).decode()}
        t0 = time.time()

        def _ocr():
            return self.client.ocr.process(
                model=self.s.ocr_model, document=doc, pages=page_indices,
                table_format="markdown", include_blocks=False, confidence_scores_granularity="page",
            )

        resp = self._retry(_ocr, tag="ocr", model=self.s.ocr_model)
        out = []
        for p in resp.pages:
            conf = getattr(p, "confidence_scores", None)
            out.append({
                "index": p.index,
                "markdown": p.markdown,
                "tables": [t.content for t in (getattr(p, "tables", None) or [])],
                "confidence": getattr(conf, "average_page_confidence_score", None) if conf else None,
            })
        usage = usage_dict(getattr(resp, "usage", None))
        self._log(
            kind="http", tag="ocr", model=self.s.ocr_model, cached=False, ok=True,
            pages=len(page_indices), seconds=round(time.time() - t0, 2), usage=usage,
        )
        self._cache_put(key, out)
        return out
