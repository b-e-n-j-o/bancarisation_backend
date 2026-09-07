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
from api.ocr.extractions.extract_arrete import extraire_depuis_markdown, vers_lignes_db
from api.ocr.ocr_mistral import pdf_vers_markdown

logger = logging.getLogger("arrete.service")


def ocr_pdf_vers_markdown(pdf_bytes: bytes, filename: str = "arrete.pdf") -> str:
    """OCR Mistral → markdown complet."""
    with tempfile.TemporaryDirectory(prefix="arrete_ocr_") as tmp:
        out_dir = Path(tmp)
        md_path, n_pages = pdf_vers_markdown(pdf_bytes, filename, out_dir)
        logger.info("OCR arrêté : %s pages → %s", n_pages, md_path.name)
        return md_path.read_text(encoding="utf-8")


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
    OCR → extraction LLM (contrat riche) → UPDATE `arrete` + INSERT prescriptions.
    Les valeurs formulaire (type / référence / date notif) priment si renseignées.
    """
    markdown = ocr_pdf_vers_markdown(pdf_bytes, filename)
    extraction = extraire_depuis_markdown(markdown)
    arrete_row, presc_rows = vers_lignes_db(
        extraction,
        projet_id=str(projet_id),
        document_id=str(document_id) if document_id else None,
    )

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
    extraction = extraire_depuis_markdown(markdown)
    return {
        "extraction": extraction.model_dump(mode="json"),
        "usage": {},
    }
