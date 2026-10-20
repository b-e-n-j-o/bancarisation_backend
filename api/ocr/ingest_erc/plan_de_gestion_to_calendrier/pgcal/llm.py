"""Client Mistral unique : chat JSON (texte), chat JSON (image), OCR.

- Sortie structurée : `response_format=json_schema` dérivé du modèle Pydantic,
  repli sur `json_object` si l'API refuse le schéma, puis validation Pydantic
  et une tentative de réparation en cas d'erreur.
- Cache disque : chaque appel est rejouable sans coût (clé = hash de la requête).
- Journal : une ligne JSON par appel dans `llm_calls.jsonl` du run.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    pass


def _texte_contenu(content) -> str:
    """Texte utile d'une réponse. mistral-large-4 préfixe un ThinkChunk (réflexion) à ignorer."""
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


def _ligne_appel(kw: dict) -> str:
    """Une ligne de log backend, sans prompt ni clé."""
    bits = ["[pgcal]", "←" if "cached" in kw or "seconds" in kw else "·", str(kw.get("tag") or "appel")]
    if kw.get("kind"):
        bits.append(str(kw["kind"]))
    if kw.get("model"):
        bits.append(f"modèle={kw['model']}")
    if kw.get("cached") is True:
        bits.append("cache")
    elif kw.get("cached") is False:
        bits.append("api")
    if kw.get("n") is not None:
        bits.append(f"n={kw['n']}")
    if kw.get("ok") is not None:
        bits.append(f"ok={kw['ok']}")
    if kw.get("pages") is not None:
        bits.append(f"pages={kw['pages']}")
    if kw.get("seconds") is not None:
        bits.append(f"{kw['seconds']}s")
    usage = kw.get("usage") or {}
    if isinstance(usage, dict):
        entree = usage.get("prompt_tokens", usage.get("input_tokens"))
        sortie = usage.get("completion_tokens", usage.get("output_tokens"))
        if entree or sortie:
            bits.append(f"tokens={entree or '?'}/{sortie or '?'}")
    return "  ".join(bits)


class MistralClient:
    def __init__(self, settings: Settings, run_dir: Path):
        self.s = settings
        self.cache_dir = settings.runs_dir / ".cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = run_dir / "llm_calls.jsonl"
        self._client = None

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
        with self.log_path.open("a") as f:
            f.write(json.dumps(kw, ensure_ascii=False, default=str) + "\n")
        print(f"   {_ligne_appel(kw)}", flush=True)

    @staticmethod
    def _retry(fn, tries: int = 4):
        for i in range(tries):
            try:
                return fn()
            except LLMUnavailable:
                raise
            except Exception as e:  # noqa: BLE001 — SDK lève des types variés
                msg = str(e).replace("\n", " ")
                if i == tries - 1 or any(c in msg for c in ("400", "401", "403", "422")):
                    print(f"   [pgcal] appel en échec ({type(e).__name__}) — {msg[:240]}", flush=True)
                    raise
                wait = 2 ** i * 2
                print(
                    f"   [pgcal] ⏳ limite ou erreur transitoire, nouvel essai "
                    f"{i + 2}/{tries} dans {wait}s — {msg[:180]}",
                    flush=True,
                )
                time.sleep(wait)

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
            self._log(tag=tag, kind="vision" if isinstance(user, list) else "chat",
                      model=model, cached=True, n=n)
            return [schema.model_validate(c) for c in cached]
        if self.s.offline:
            raise LLMUnavailable("mode hors ligne : aucun appel API")

        kind = "vision" if isinstance(user, list) else "chat"
        print(f"   [pgcal] → {kind}  {tag}  modèle={model}  n={n}", flush=True)
        t0 = time.time()
        raw_choices, usage = self._complete(model, messages, schema, temperature, n, cache_key_hint)
        out: list[T] = []
        for raw in raw_choices:
            obj = self._validate_or_repair(schema, raw, model)
            if obj is not None:
                out.append(obj)
        self._log(tag=tag, kind=kind, model=model, cached=False, n=n, ok=len(out),
                  seconds=round(time.time() - t0, 1), usage=usage)
        self._cache_put(key, [o.model_dump() for o in out])
        return out

    def _complete(self, model, messages, schema, temperature, n, cache_key_hint):
        rf_schema = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
        }
        kwargs = dict(model=model, messages=messages, temperature=temperature, max_tokens=16_000)
        if temperature is not None and temperature <= 0:
            # mistral-large-4 : temperature 0 = tirage glouton, top_p doit valoir 1.
            kwargs["top_p"] = 1
        if cache_key_hint:
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
                choices.append(r.choices[0].message.content)
                usage = getattr(r, "usage", None) and r.usage.model_dump()
            return choices, usage
        usage = getattr(resp, "usage", None) and resp.usage.model_dump()
        return [c.message.content for c in resp.choices], usage

    def _validate_or_repair(self, schema: type[T], raw, model: str) -> T | None:
        text = _texte_contenu(raw)
        if not text:
            print(f"   [pgcal] réponse vide  schéma={schema.__name__}", flush=True)
            return None
        try:
            return schema.model_validate_json(text)
        except (ValidationError, json.JSONDecodeError) as e:
            err = str(e)[:2000]
        print(f"   [pgcal] → réparation JSON  schéma={schema.__name__}  modèle={model}", flush=True)
        repair_msgs = [
            {"role": "system", "content": "Tu corriges un JSON pour qu'il respecte un schéma. Réponds uniquement par le JSON corrigé."},
            {"role": "user", "content": f"Schéma :\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n\nErreurs :\n{err}\n\nJSON :\n{text}"},
        ]
        try:
            r = self._retry(lambda: self.client.chat.complete(
                model=model, messages=repair_msgs, temperature=0, top_p=1,
                response_format={"type": "json_object"}))
            obj = schema.model_validate_json(_texte_contenu(r.choices[0].message.content))
            print(f"   [pgcal] ← réparation JSON  schéma={schema.__name__}  ok", flush=True)
            return obj
        except Exception as e:  # noqa: BLE001
            print(f"   [pgcal] ← réparation JSON  schéma={schema.__name__}  échec — {e}", flush=True)
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
            self._log(tag="ocr", kind="ocr", model=self.s.ocr_model, cached=True, pages=len(page_indices))
            return cached
        print(
            f"   [pgcal] → ocr  modèle={self.s.ocr_model}  pages={page_indices}",
            flush=True,
        )
        doc = {"type": "document_url", "document_url": "data:application/pdf;base64," + base64.b64encode(data).decode()}
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
        self._log(tag="ocr", kind="ocr", model=self.s.ocr_model, cached=False,
                  pages=len(page_indices), seconds=round(time.time() - t0, 1))
        self._cache_put(key, out)
        return out
