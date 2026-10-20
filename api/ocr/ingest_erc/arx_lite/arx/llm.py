"""Client Mistral unique : chat JSON (texte), chat JSON (image), OCR. (identique à pgcal)

- Sortie structurée : `response_format=json_schema` dérivé du modèle Pydantic,
  repli sur `json_object` si l'API refuse le schéma, puis validation Pydantic
  et une tentative de réparation en cas d'erreur.
- Cache disque : chaque appel est rejouable sans coût (clé = hash de la requête).
- Journal : une ligne JSON par appel dans `llm_calls.jsonl` du run.
"""
from __future__ import annotations

import base64
import hashlib
import inspect
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings

logger = logging.getLogger("uvicorn.error")

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


def _accepte_parametre(fn, nom: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if nom in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _usage_lisible(usage: Any) -> str:
    if usage is None:
        return "tokens=?"
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return "tokens=?"
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    extra = ""
    for cle in ("cost", "total_cost", "cost_usd"):
        if usage.get(cle) is not None:
            extra += f" cout={usage[cle]}"
    return f"prompt={prompt} completion={completion} total={total}{extra}"


class LLMUnavailable(RuntimeError):
    pass


class MistralClient:
    def __init__(self, settings: Settings, run_dir: Path):
        self.s = settings
        self.cache_dir = settings.runs_dir / ".cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = run_dir / "llm_calls.jsonl"
        self._client = None
        self._lock = threading.Lock()

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
        with self._lock, self.log_path.open("a") as f:
            f.write(json.dumps(kw, ensure_ascii=False, default=str) + "\n")
        logger.info(
            "arx_lite appel tag=%s modele=%s cache=%s n=%s ok=%s duree=%ss %s",
            kw.get("tag"),
            kw.get("model"),
            kw.get("cached"),
            kw.get("n", kw.get("pages")),
            kw.get("ok", True),
            kw.get("seconds"),
            _usage_lisible(kw.get("usage")),
        )

    @staticmethod
    def _retry(fn, tries: int = 4):
        for i in range(tries):
            try:
                return fn()
            except (LLMUnavailable, TypeError):
                raise
            except Exception as e:  # noqa: BLE001 — SDK lève des types variés
                msg = str(e)
                final = i == tries - 1 or any(c in msg for c in ("400", "401", "403", "422"))
                logger.warning("arx_lite essai %s/%s en échec : %s", i + 1, tries, msg[:300])
                if final:
                    raise
                time.sleep(2 ** i * 2)

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
            self._log(tag=tag, model=model, cached=True, n=n)
            return [schema.model_validate(c) for c in cached]
        if self.s.offline:
            raise LLMUnavailable("mode hors ligne : aucun appel API")

        taille = len(user) if isinstance(user, str) else sum(len(str(p)) for p in user)
        logger.info(
            "arx_lite appel en cours tag=%s schema=%s modele=%s n=%s prompt=%s car.",
            tag, schema.__name__, model, n, taille,
        )
        t0 = time.time()
        stop_attente = threading.Event()

        def _attente() -> None:
            while not stop_attente.wait(30):
                logger.info(
                    "arx_lite toujours en attente tag=%s modele=%s depuis %.0fs (pas de token avant la fin : appel non streamé)",
                    tag, model, time.time() - t0,
                )

        threading.Thread(target=_attente, daemon=True).start()
        try:
            raw_choices, usage = self._complete(model, messages, schema, temperature, n, cache_key_hint)
        finally:
            stop_attente.set()
        out: list[T] = []
        for raw in raw_choices:
            obj = self._validate_or_repair(schema, raw, model)
            if obj is not None:
                out.append(obj)
        self._log(tag=tag, model=model, cached=False, n=n, ok=len(out),
                  seconds=round(time.time() - t0, 1), usage=usage)
        self._cache_put(key, [o.model_dump() for o in out])
        return out

    def _complete(self, model, messages, schema, temperature, n, cache_key_hint):
        rf_schema = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
        }
        kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=1,
            max_tokens=getattr(self.s, "max_tokens", 16_000),
        )
        if cache_key_hint and _accepte_parametre(self.client.chat.complete, "prompt_cache_key"):
            kwargs["prompt_cache_key"] = cache_key_hint

        def call(rf, n_):
            return self.client.chat.complete(response_format=rf, n=n_, **kwargs)

        try:
            resp = self._retry(lambda: call(rf_schema, n))
        except LLMUnavailable:
            raise
        except Exception:  # schéma refusé ou `n` non supporté : repli
            resp = None
        if resp is None:
            rf_obj = {"type": "json_object"}
            choices, usage = [], {}
            for _ in range(n):
                r = self._retry(lambda: call(rf_obj, 1))
                choices.append(_texte_contenu(r.choices[0].message.content))
                usage = getattr(r, "usage", None) and r.usage.model_dump()
            return choices, usage
        usage = getattr(resp, "usage", None) and resp.usage.model_dump()
        return [_texte_contenu(c.message.content) for c in resp.choices], usage

    def _validate_or_repair(self, schema: type[T], raw, model: str) -> T | None:
        text = _texte_contenu(raw)
        try:
            return schema.model_validate_json(text)
        except ValidationError as e:
            err = str(e)[:2000]
        repair_msgs = [
            {"role": "system", "content": "Tu corriges un JSON pour qu'il respecte un schéma. Réponds uniquement par le JSON corrigé."},
            {"role": "user", "content": f"Schéma :\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n\nErreurs :\n{err}\n\nJSON :\n{text}"},
        ]
        logger.info("arx_lite réparation JSON en cours modele=%s schema=%s", model, schema.__name__)
        t0 = time.time()
        try:
            r = self._retry(lambda: self.client.chat.complete(
                model=model, messages=repair_msgs, temperature=0, top_p=1,
                response_format={"type": "json_object"}))
            self._log(
                tag="repair", model=model, cached=False, n=1, ok=True,
                seconds=round(time.time() - t0, 1),
                usage=getattr(r, "usage", None) and r.usage.model_dump(),
            )
            return schema.model_validate_json(_texte_contenu(r.choices[0].message.content))
        except Exception:  # noqa: BLE001
            self._log(tag="repair", model=model, cached=False, n=1, ok=False, seconds=round(time.time() - t0, 1))
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
            self._log(tag="ocr", model=self.s.ocr_model, cached=True, pages=len(page_indices), seconds=0)
            return cached
        doc = {"type": "document_url", "document_url": "data:application/pdf;base64," + base64.b64encode(data).decode()}
        logger.info("arx_lite OCR en cours modele=%s pages=%s", self.s.ocr_model, len(page_indices))
        t0 = time.time()
        resp = self._retry(lambda: self.client.ocr.process(
            model=self.s.ocr_model, document=doc, pages=page_indices,
            table_format="markdown", include_blocks=False, confidence_scores_granularity="page",
        ))
        out = []
        for p in resp.pages:
            conf = getattr(p, "confidence_scores", None)
            out.append({
                "index": p.index,
                "markdown": p.markdown,
                "tables": [t.content for t in (getattr(p, "tables", None) or [])],
                "confidence": getattr(conf, "average_page_confidence_score", None) if conf else None,
            })
        self._log(
            tag="ocr", model=self.s.ocr_model, cached=False, pages=len(page_indices), ok=True,
            seconds=round(time.time() - t0, 1),
            usage=getattr(resp, "usage", None) and resp.usage.model_dump(),
        )
        self._cache_put(key, out)
        return out
