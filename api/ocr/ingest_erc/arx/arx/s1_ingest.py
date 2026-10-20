"""Étape 1 — Ingestion : PDF -> texte par page, nettoyé.

- Texte natif « layout » (pdfplumber, licence MIT) : les tableaux
  d'échéances restent alignés, ce qui aide beaucoup le LLM.
- Pages sans texte (scans) -> OCR Mistral (markdown, tableaux inclus).
- Nettoyage : numéros de page (« 3/20 ») et lignes répétées en en-tête/pied
  sur une majorité de pages (adresse de la préfecture…).
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

import pdfplumber
from pydantic import BaseModel

from .config import Settings
from .llm import LLMUnavailable, MistralClient

PAGE_NUM = re.compile(r"^\s*(?:page\s*)?\d{1,3}\s*(?:/|sur)\s*\d{1,3}\s*$", re.I)


class Page(BaseModel):
    number: int
    source: str = "native"
    text: str = ""


class DocumentText(BaseModel):
    path: str
    sha256: str
    n_pages: int
    pages: list[Page]
    removed_lines: list[str] = []

    def page(self, n: int) -> Page:
        return self.pages[n - 1]


def _native(pdf_path: Path, n: int) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages[:n]
        return [(p.extract_text(layout=True) or "") for p in pages]


def _clean(pages: list[str]) -> tuple[list[str], list[str]]:
    """Retire numéros de page et lignes répétées (en-têtes / pieds de page)."""
    norm = lambda l: re.sub(r"\s+", " ", l).strip()  # noqa: E731
    counts = Counter(x for p in pages for x in {norm(l) for l in p.splitlines() if norm(l)})
    threshold = max(3, int(0.5 * len(pages)))
    repeated = {l for l, c in counts.items() if c >= threshold and len(l) > 3}
    out, removed = [], set()
    for p in pages:
        keep = []
        for l in p.splitlines():
            n = norm(l)
            if PAGE_NUM.match(n) or n in repeated:
                removed.add(n)
                continue
            keep.append(l.rstrip())
        out.append("\n".join(keep))
    return out, sorted(removed)[:30]


def ingest(pdf_path: Path, settings: Settings, llm: MistralClient) -> tuple[DocumentText, list[str]]:
    issues: list[str] = []
    data = pdf_path.read_bytes()
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
    texts = _native(pdf_path, n)
    sources = ["native"] * n
    to_ocr = [i for i, t in enumerate(texts) if settings.force_ocr or len(re.sub(r"\s", "", t)) < settings.ocr_min_chars]
    if to_ocr:
        try:
            for r in llm.ocr_pages(pdf_path, to_ocr):
                texts[r["index"]] = r["markdown"] + ("\n" + "\n".join(r["tables"]) if r["tables"] else "")
                sources[r["index"]] = "ocr"
        except LLMUnavailable as e:
            issues.append(f"OCR non réalisé sur {len(to_ocr)} page(s) peu textuelles ({e}).")
    cleaned, removed = _clean(texts)
    pages = [Page(number=i + 1, source=sources[i], text=t) for i, t in enumerate(cleaned)]
    return DocumentText(path=str(pdf_path), sha256=hashlib.sha256(data).hexdigest(), n_pages=n,
                        pages=pages, removed_lines=removed), issues


def as_prompt(doc: DocumentText) -> str:
    parts = []
    for p in doc.pages:
        body = re.sub(r"[ \t]{3,}", "   ", p.text)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        parts.append(f"[PAGE {p.number}]\n{body}")
    return "\n\n".join(parts)
