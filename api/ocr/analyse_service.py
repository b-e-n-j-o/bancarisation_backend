"""Orchestration bout-en-bout : PDF → OCR → extraction → base."""

from __future__ import annotations

import logging
from pathlib import Path

from .analyse_jobs import avancer, demarrer, echouer, terminer, work_dir
from .db_env import load_db_env
from .ocr_mistral import pdf_vers_markdown
from .pipeline_service import executer

load_db_env()

log = logging.getLogger("analyse")


def lancer_analyse(
    projet_id: str,
    pdf_bytes: bytes,
    filename: str,
    *,
    replace: bool = True,
) -> None:
    """Fonction de fond (BackgroundTasks) — ne pas appeler depuis une requête synchrone."""
    load_db_env()
    try:
        demarrer(projet_id, filename)
        wd = work_dir(projet_id)
        ocr_dir = wd / "ocr_output"
        (wd / "source.pdf").write_bytes(pdf_bytes)

        avancer(projet_id, "ocr", "Envoi du PDF à Mistral OCR…")
        full_md, nb_pages = pdf_vers_markdown(pdf_bytes, filename, ocr_dir)
        markdown = full_md.read_text(encoding="utf-8")
        log.info("OCR terminé — %d pages — projet %s", nb_pages, projet_id)

        recap = executer(
            projet_id,
            markdown,
            wd,
            fichier_nom=filename,
            replace=replace,
            on_progress=lambda etape, detail: avancer(projet_id, etape, detail),
        )
        recap["pages_ocr"] = nb_pages
        terminer(projet_id, recap)
        log.info("Analyse terminée — projet %s", projet_id)
    except Exception as exc:
        log.exception("Analyse échouée — projet %s", projet_id)
        echouer(projet_id, str(exc))
