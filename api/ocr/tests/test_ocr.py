#!/usr/bin/env python3
"""
Test OCR Mistral (OCR 4) sur un plan de gestion de compensation écologique.

Prérequis :
    pip install mistralai pypdf python-dotenv
    export MISTRAL_API_KEY="ta_cle_ici"   # ou backend/api/ocr/.env

Usage :
    python test_ocr.py
    python test_ocr.py /chemin/vers/un_autre.pdf
    python test_ocr.py --start 3 --end 5
    python test_ocr.py mon.pdf --start 3 --end 5

Les numéros de page (--start / --end) sont 1-indexés et inclusifs.
Le PDF est tronqué localement avant l'upload Mistral (pas de filtre côté API).

Produit dans ./ocr_output/ :
    - full.md            : markdown concaténé de toutes les pages (à inspecter en premier)
    - page_XX.md         : markdown page par page (numéro = page d'origine du PDF)
    - raw_response.json  : réponse OCR brute (bounding boxes, blocs, scores de confiance)

Ce qu'il faut regarder dans full.md :
    1. Les FRISES temporelles (bandeaux 2019…2033 / 2034…2048) : est-ce que l'alignement
       année ↔ action est préservé, ou est-ce que ça sort à plat ? C'est LE point dur.
    2. Les tableaux : structure conservée ?
    3. Le corps des fiches (périodicité, récurrence, fenêtres) : ça devrait être quasi parfait.
"""

import argparse
import io
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mistralai import Mistral
from pypdf import PdfReader, PdfWriter

load_dotenv()

# --- Configuration -----------------------------------------------------------

DEFAULT_PDF = (
    "/Volumes/T7/Travaux_Freelance/KERELIA/CUAs/COMPENSATION_PARCELLE/"
    "BANCARISATION_COMPENSATION/backend/api/ocr/Plan_de_gestion_exemple.pdf"
)

# "mistral-ocr-latest" pointe vers la dernière version (OCR 4 depuis le 23/06/2026).
# Épingle "mistral-ocr-4-0" si tu veux verrouiller la version.
OCR_MODEL = "mistral-ocr-latest"

OUTPUT_DIR = Path("./ocr_output")


# --- Utilitaires PDF ---------------------------------------------------------

def extract_pdf_pages(pdf_path: Path, start: int | None, end: int | None) -> tuple[bytes, int, int]:
    """Extrait une plage de pages (1-indexée, inclusive) et retourne les bytes du PDF tronqué."""
    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)

    page_start = start if start is not None else 1
    page_end = end if end is not None else total

    if page_start < 1 or page_end < page_start or page_end > total:
        sys.exit(
            f"❌ Plage invalide : pages {page_start}–{page_end} "
            f"(le PDF en contient {total}, numérotation 1-indexée)."
        )

    writer = PdfWriter()
    for i in range(page_start - 1, page_end):
        writer.add_page(reader.pages[i])

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), page_start, page_end


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OCR Mistral sur un PDF (optionnellement tronqué en local)."
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        default=DEFAULT_PDF,
        help="Chemin du PDF à analyser",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=None,
        metavar="N",
        help="Première page à inclure (1-indexée, inclusive)",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        metavar="N",
        help="Dernière page à inclure (1-indexée, inclusive)",
    )
    return parser.parse_args()


# --- Pipeline ----------------------------------------------------------------

def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf)

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        sys.exit("❌ MISTRAL_API_KEY absente. Fais : export MISTRAL_API_KEY='...' ou crée backend/api/ocr/.env")

    if not pdf_path.exists():
        sys.exit(f"❌ PDF introuvable : {pdf_path}")

    if args.start is not None or args.end is not None:
        pdf_bytes, page_start, page_end = extract_pdf_pages(pdf_path, args.start, args.end)
        upload_name = f"{pdf_path.stem}_p{page_start}-{page_end}.pdf"
        print(f"✂️  PDF tronqué : pages {page_start}–{page_end} sur {pdf_path.name}")
    else:
        pdf_bytes = pdf_path.read_bytes()
        page_start = 1
        upload_name = pdf_path.name
        print(f"📄 PDF complet : {pdf_path.name}")

    client = Mistral(api_key=api_key)
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 1. Upload du PDF (tronqué ou complet) vers le cloud Mistral, puis URL signée.
    print(f"📤 Upload de {upload_name} …")
    uploaded = client.files.upload(
        file={"file_name": upload_name, "content": pdf_bytes},
        purpose="ocr",
    )
    signed = client.files.get_signed_url(file_id=uploaded.id)

    # 2. OCR.
    print(f"🔍 OCR ({OCR_MODEL}) …")
    ocr = client.ocr.process(
        model=OCR_MODEL,
        document={"type": "document_url", "document_url": signed.url},
        include_image_base64=False,
    )

    # 3. Dump brut (bbox, blocs, confiance, métadonnées).
    raw = ocr.model_dump()
    (OUTPUT_DIR / "raw_response.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 4. Markdown par page + concaténé (numéros = pages d'origine du PDF source).
    full_parts = []
    for i, page in enumerate(ocr.pages):
        orig_page = page_start + i
        md = page.markdown or ""
        (OUTPUT_DIR / f"page_{orig_page:02d}.md").write_text(md, encoding="utf-8")
        full_parts.append(f"\n\n<!-- ===== PAGE {orig_page} ===== -->\n\n{md}")
    (OUTPUT_DIR / "full.md").write_text("".join(full_parts), encoding="utf-8")

    # 5. Résumé console.
    nb_pages = len(ocr.pages)
    usage = raw.get("usage_info") or {}
    print(f"✅ {nb_pages} page(s) traitée(s).")
    print(f"   Pages facturées : {usage.get('pages_processed', 'n/a')}")
    print(f"   Sorties dans    : {OUTPUT_DIR.resolve()}")
    print("   → Ouvre full.md et vérifie d'abord le rendu des FRISES temporelles.")

    # Nettoyage optionnel du fichier uploadé côté Mistral.
    try:
        client.files.delete(file_id=uploaded.id)
    except Exception:
        pass


if __name__ == "__main__":
    main()
