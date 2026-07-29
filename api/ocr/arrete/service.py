"""Pipeline OCR + extraction LLM pour un arrêté PDF."""

from __future__ import annotations

import logging
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from api.controle import crud
from api.controle.schemas import ArreteOut
from api.ocr.arrete.extraction_arrete import extract_arrete, to_db_rows
from api.ocr.ocr_mistral import pdf_vers_markdown

logger = logging.getLogger("arrete.service")


def ocr_pdf_vers_markdown(pdf_bytes: bytes, filename: str = "arrete.pdf") -> str:
    """OCR Mistral → markdown complet."""
    with tempfile.TemporaryDirectory(prefix="arrete_ocr_") as tmp:
        out_dir = Path(tmp)
        md_path, n_pages = pdf_vers_markdown(pdf_bytes, filename, out_dir)
        logger.info("OCR arrêté : %s pages → %s", n_pages, md_path.name)
        return md_path.read_text(encoding="utf-8")


def extraire_depuis_markdown(markdown: str) -> tuple[Any, dict]:
    """Appel LLM d'extraction structurée."""
    return extract_arrete(markdown)


def extraire_et_persister(
    *,
    pdf_bytes: bytes,
    arrete_id: UUID,
    projet_id: UUID,
    document_id: UUID | None,
    type_form: str | None = None,
    reference_form: str | None = None,
    date_notification_form: date | None = None,
    filename: str = "arrete.pdf",
) -> ArreteOut:
    """
    OCR → extraction LLM → UPDATE `arrete` + INSERT `arrete_prescription`.
    Les valeurs formulaire (type / référence / date notif) priment si renseignées.
    """
    markdown = ocr_pdf_vers_markdown(pdf_bytes, filename)
    extraction, usage = extraire_depuis_markdown(markdown)
    arrete_row, presc_rows = to_db_rows(
        extraction,
        str(projet_id),
        str(document_id) if document_id else None,
        usage,
    )

    # Priorité formulaire sur les champs saisis à l'upload
    if type_form:
        arrete_row["type"] = type_form
    if reference_form and reference_form.strip():
        arrete_row["reference"] = reference_form.strip()
    if date_notification_form is not None:
        arrete_row["date_notification"] = date_notification_form.isoformat()

    return crud.persister_extraction_arrete(
        arrete_id,
        arrete_row=arrete_row,
        prescriptions=presc_rows,
    )


def extraire_fichier_seul(pdf_bytes: bytes, filename: str = "arrete.pdf") -> dict:
    """Endpoint debug : OCR + extraction sans persistance."""
    markdown = ocr_pdf_vers_markdown(pdf_bytes, filename)
    extraction, usage = extraire_depuis_markdown(markdown)
    return {
        "extraction": extraction.model_dump(),
        "usage": usage,
    }
