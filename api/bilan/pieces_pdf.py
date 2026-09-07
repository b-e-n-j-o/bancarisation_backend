"""Pièces PDF d'occurrence : intercalaires + fusion dans le bilan.

Les octets ne sont jamais stockés dans le snapshot JSON : uniquement au rendu.
On colle les pages originales (pypdf) derrière un intercalaire cliquable.
"""

from __future__ import annotations

import io
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

MAX_PAGES_PDF = 40


def est_pdf(doc: dict[str, Any]) -> bool:
    mime = (doc.get("type_mime") or "").lower()
    if mime == "application/pdf":
        return True
    nom = (doc.get("nom_fichier") or doc.get("nom") or "").lower()
    return nom.endswith(".pdf")


def _charger_octets(doc_id: str) -> bytes | None:
    from api.documents.crud_document import DocumentServiceError, get_document_content

    try:
        content, _mime, _filename = get_document_content(UUID(str(doc_id)))
    except (DocumentServiceError, ValueError) as exc:
        logger.warning("PDF %s inaccessible pour le bilan : %s", doc_id, exc)
        return None
    except Exception:
        logger.exception("PDF %s : erreur inattendue au téléchargement.", doc_id)
        return None
    return content or None


def _nb_pages(raw: bytes) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(raw)).pages)
    except Exception:
        logger.exception("PDF illisible (ouverture).")
        return None


def enrichir_pdfs(
    lignes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Références D-n sur les PDF, groupes d'annexe, octets à fusionner.

    S'appuie sur `docs_non_image` (après enrichir_photos). Mutates `lignes`.
    """
    n = 0
    groupes: list[dict[str, Any]] = []
    fusion: list[dict[str, Any]] = []
    for ligne in lignes:
        source = ligne.get("docs_non_image")
        if source is None:
            source = [
                d
                for d in (ligne.get("documents") or [])
                if isinstance(d, dict) and not d.get("est_image")
            ]
        pdfs: list[dict[str, Any]] = []
        autres: list[dict[str, Any]] = []
        for doc in source:
            if not isinstance(doc, dict) or not est_pdf(doc):
                if isinstance(doc, dict):
                    autres.append(doc)
                continue
            n += 1
            ref = f"D-{n}"
            doc["est_pdf"] = True
            doc["ref_pdf"] = ref
            doc_id = str(doc.get("id") or "")
            raw = _charger_octets(doc_id) if doc_id else None
            nb = _nb_pages(raw) if raw else None
            ok = bool(raw) and nb is not None and nb > 0
            piece = {
                "id": doc_id,
                "ref": ref,
                "nom": doc.get("nom") or doc.get("nom_fichier") or "Document",
                "date_document": doc.get("date_document"),
                "categorie": doc.get("categorie"),
                "nb_pages": nb or 0,
                "nb_pages_incluses": min(nb or 0, MAX_PAGES_PDF) if ok else 0,
                "tronque": bool(ok and nb and nb > MAX_PAGES_PDF),
                "ok": ok,
            }
            pdfs.append(piece)
            if ok and raw:
                fusion.append({"id": doc_id, "content": raw, "ref": ref})
        ligne["pdfs"] = pdfs
        ligne["docs_autres"] = autres
        ligne["docs_non_image"] = autres
        if pdfs:
            groupes.append({
                "occurrence_id": ligne.get("id"),
                "code": ligne.get("code"),
                "titre": ligne.get("titre") or "Sans titre",
                "pdfs": pdfs,
            })
    return groupes, fusion


def _page_index_dest(reader: Any, nom: str) -> int | None:
    dests = getattr(reader, "named_destinations", None) or {}
    dest = dests.get(nom) or dests.get("/" + nom)
    if dest is None:
        return None
    page = getattr(dest, "page", None)
    if page is None and isinstance(dest, dict):
        page = dest.get("/Page")
    if page is None:
        return None
    page_ref = getattr(page, "indirect_reference", page)
    for i, p in enumerate(reader.pages):
        if p is page or p == page:
            return i
        if getattr(p, "indirect_reference", None) == page_ref:
            return i
    return None


def fusionner_pdfs_annexes(pdf_bilan: bytes, pieces: list[dict[str, Any]]) -> bytes:
    """Insère les pages de chaque pièce juste après son intercalaire."""
    if not pieces:
        return pdf_bilan

    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(pdf_bilan))
    insertions: list[tuple[int, dict[str, Any]]] = []
    for piece in pieces:
        idx = _page_index_dest(reader, f"annexe-doc-{piece['id']}")
        if idx is None:
            logger.warning(
                "Intercalaire introuvable pour %s — pièce ajoutée en fin de document.",
                piece.get("ref") or piece.get("id"),
            )
            idx = len(reader.pages) - 1
        insertions.append((idx, piece))

    # Du dernier intercalaire au premier : les indices amont restent valides.
    insertions.sort(key=lambda x: x[0], reverse=True)

    writer = PdfWriter()
    writer.append(io.BytesIO(pdf_bilan), import_outline=False)

    for idx, piece in insertions:
        extra_buf = io.BytesIO(piece["content"])
        try:
            extra = PdfReader(extra_buf)
            n_pages = len(extra.pages)
            if n_pages == 0:
                continue
            n_keep = min(n_pages, MAX_PAGES_PDF)
            writer.merge(
                position=idx + 1,
                fileobj=io.BytesIO(piece["content"]),
                pages=list(range(n_keep)),
                import_outline=False,
            )
        except Exception:
            logger.exception(
                "Fusion impossible pour la pièce %s.",
                piece.get("ref") or piece.get("id"),
            )

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
