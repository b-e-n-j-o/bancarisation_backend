"""OCR Mistral — PDF → markdown (extrait de tests/test_ocr.py)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mistralai import Mistral

OCR_MODEL = "mistral-ocr-latest"


def pdf_vers_markdown(
    pdf_bytes: bytes,
    filename: str,
    output_dir: Path,
) -> tuple[Path, int]:
    """
    Envoie un PDF à Mistral OCR et écrit full.md + pages dans output_dir.
    Retourne (chemin full.md, nombre de pages).
    """
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY absente.")

    output_dir.mkdir(parents=True, exist_ok=True)
    client = Mistral(api_key=api_key)

    uploaded = client.files.upload(
        file={"file_name": filename, "content": pdf_bytes},
        purpose="ocr",
    )
    signed = client.files.get_signed_url(file_id=uploaded.id)

    try:
        ocr = client.ocr.process(
            model=OCR_MODEL,
            document={"type": "document_url", "document_url": signed.url},
            include_image_base64=False,
        )

        raw = ocr.model_dump()
        (output_dir / "raw_response.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        full_parts: list[str] = []
        for i, page in enumerate(ocr.pages):
            page_num = i + 1
            md = page.markdown or ""
            (output_dir / f"page_{page_num:02d}.md").write_text(md, encoding="utf-8")
            full_parts.append(f"\n\n<!-- ===== PAGE {page_num} ===== -->\n\n{md}")

        full_path = output_dir / "full.md"
        full_path.write_text("".join(full_parts), encoding="utf-8")
        return full_path, len(ocr.pages)
    finally:
        try:
            client.files.delete(file_id=uploaded.id)
        except Exception:
            pass
