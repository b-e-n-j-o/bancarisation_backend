"""Étape 1 — Ingestion : PDF -> texte page par page (natif, sinon OCR Mistral).

Sorties : `DocumentText` (texte « layout » qui conserve les colonnes, numéro
imprimé de chaque page, source native/OCR). Fournit aussi le rendu PNG d'une
page, utilisé par l'étape 3 pour la lecture vision des grilles.
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import pdfplumber

from .config import Settings
from .llm import LLMUnavailable, MistralClient
from .schemas import DocumentText, Page

_PRINTED_NUM = re.compile(r"^\s*(\d{1,4})\s*$")


def _native_text(pdf_path: Path, n_pages: int) -> list[str]:
    """Texte avec mise en page conservée (pdfplumber, licence MIT)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [(p.extract_text(layout=True) or "") for p in pdf.pages[:n_pages]]


def _printed_number(text: str) -> str | None:
    lines = [l for l in text.splitlines() if l.strip()]
    for line in reversed(lines[-6:]):  # pied de page en priorité
        m = _PRINTED_NUM.match(line)
        if m:
            return m.group(1)
    for line in lines[:3]:
        m = _PRINTED_NUM.match(line)
        if m:
            return m.group(1)
    return None


def ingest(pdf_path: Path, settings: Settings, llm: MistralClient) -> tuple[DocumentText, list[str]]:
    issues: list[str] = []
    data = pdf_path.read_bytes()
    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
    texts = _native_text(pdf_path, n_pages)

    pages = []
    for i, t in enumerate(texts):
        chars = len(re.sub(r"\s", "", t))
        pages.append(Page(index=i, number=i + 1, char_count=chars, text=t, printed_number=_printed_number(t)))

    scanned = [p.index for p in pages if p.char_count < settings.ocr_min_chars]
    if scanned:
        try:
            for r in llm.ocr_pages(pdf_path, scanned):
                p = pages[r["index"]]
                p.source, p.text = "ocr", r["markdown"]
                p.ocr_tables, p.ocr_confidence = r["tables"], r["confidence"]
                p.char_count = len(re.sub(r"\s", "", p.text))
                p.printed_number = p.printed_number or _printed_number(p.text)
        except LLMUnavailable as e:
            issues.append(f"OCR non réalisé sur {len(scanned)} page(s) peu ou pas textuelles ({e}).")

    doc = DocumentText(path=str(pdf_path), sha256=hashlib.sha256(data).hexdigest(), n_pages=n_pages, pages=pages)
    return doc, issues


def render_page_png(pdf_path: Path, number: int, dpi: int) -> bytes:
    with pdfplumber.open(pdf_path) as pdf:
        img = pdf.pages[number - 1].to_image(resolution=dpi).original
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def estimate_tokens(text: str) -> int:
    return int(len(text) / 3.6)


def document_as_prompt(doc: DocumentText, numbers: list[int]) -> str:
    """Texte des pages au format attendu par les prompts : [PAGE n] (p. imprimée)."""
    parts = []
    for n in numbers:
        p = doc.page(n)
        printed = f" (p. imprimée {p.printed_number})" if p.printed_number else ""
        body = re.sub(r"[ \t]{3,}", "   ", p.text)       # compacte les grands blancs de mise en page
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if p.ocr_tables:
            body += "\n" + "\n".join(p.ocr_tables)
        parts.append(f"[PAGE {n}{printed}]\n{body}")
    return "\n\n".join(parts)
