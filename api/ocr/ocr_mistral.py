"""OCR Mistral — PDF → markdown (aligné sur tests/test_ocr.py).

Flux principal = upload + URL signée (comme le script de test qui marche).
Durcissements prod :
  · nom d'upload ASCII / minuscules (évite 404 « No file matches » sur accents)
  · 1 retry court si l'API Files est un peu lente
  · repli base64 si l'upload cloud échoue encore
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import unicodedata
from pathlib import Path

from mistralai import Mistral

OCR_MODEL = os.environ.get("MISTRAL_OCR_MODEL", "mistral-ocr-latest")


def nom_upload_ocr(filename: str) -> str:
    """Nom de fichier sûr pour Mistral Files : ASCII, minuscules, extension .pdf."""
    stem = Path(filename).stem
    ascii_stem = (
        unicodedata.normalize("NFKD", stem)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", ascii_stem).strip("._-").casefold()
    if not ascii_stem:
        ascii_stem = "document"
    return f"{ascii_stem}.pdf"


def _ocr_via_upload(client: Mistral, pdf_bytes: bytes, upload_name: str):
    """Même chemin que tests/test_ocr.py : files.upload → signed URL → ocr.process."""
    uploaded = client.files.upload(
        file={"file_name": upload_name, "content": pdf_bytes},
        purpose="ocr",
    )
    try:
        # Petite latence éventuelle côté Files API
        time.sleep(0.4)
        signed = client.files.get_signed_url(file_id=uploaded.id)
        return client.ocr.process(
            model=OCR_MODEL,
            document={"type": "document_url", "document_url": signed.url},
            include_image_base64=False,
        )
    finally:
        try:
            client.files.delete(file_id=uploaded.id)
        except Exception:
            pass


def _ocr_via_base64(client: Mistral, pdf_bytes: bytes):
    """Repli : data-URL base64 (pas de dépendance Files API)."""
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return client.ocr.process(
        model=OCR_MODEL,
        document={
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{b64}",
        },
        include_image_base64=False,
    )


def _appeler_ocr(client: Mistral, pdf_bytes: bytes, filename: str):
    upload_name = nom_upload_ocr(filename)
    derniere: Exception | None = None

    for tentative in range(1, 3):
        try:
            print(
                f"📄 [OCR API] upload « {filename} » → « {upload_name} » "
                f"→ {OCR_MODEL} (tentative {tentative}/2) …",
                flush=True,
            )
            return _ocr_via_upload(client, pdf_bytes, upload_name)
        except Exception as err:  # noqa: BLE001
            derniere = err
            msg = str(err)
            print(f"   ⚠️  OCR upload échoué : {msg[:220]}", flush=True)
            if tentative < 2 and ("404" in msg or "No file matches" in msg):
                time.sleep(1.0)
                continue
            break

    print("   ↪️  repli OCR en base64…", flush=True)
    try:
        return _ocr_via_base64(client, pdf_bytes)
    except Exception as err:  # noqa: BLE001
        raise RuntimeError(
            f"OCR Mistral échoué (upload puis base64) : {err}"
        ) from (derniere or err)


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
    if not pdf_bytes:
        raise RuntimeError(f"PDF vide : {filename}")

    output_dir.mkdir(parents=True, exist_ok=True)
    client = Mistral(api_key=api_key)

    ocr = _appeler_ocr(client, pdf_bytes, filename)

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
    print(f"📄 [OCR API] terminé — {len(ocr.pages)} page(s)", flush=True)
    return full_path, len(ocr.pages)
