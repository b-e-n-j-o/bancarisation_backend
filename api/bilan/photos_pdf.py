"""Chargement des photos d'occurrence pour l'annexe du PDF de bilan.

Les octets ne sont jamais stockés dans le snapshot JSON : uniquement au rendu.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

IMAGE_EXTS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".heic", ".heif", ".tif", ".tiff",
)
MAX_COTE_PX = 1400
JPEG_QUALITY = 78


def est_image(doc: dict[str, Any]) -> bool:
    mime = (doc.get("type_mime") or "").lower()
    if mime == "application/pdf":
        return False
    if mime.startswith("image/"):
        return True
    nom = (doc.get("nom_fichier") or doc.get("nom") or "").lower()
    if any(nom.endswith(ext) for ext in IMAGE_EXTS):
        return True
    return (doc.get("categorie") or "").lower() == "photos"


def _jpeg_data_uri(raw: bytes) -> str | None:
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow absent : impossible de normaliser l'image pour le PDF.")
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        im = im.convert("RGB")
        im.thumbnail((MAX_COTE_PX, MAX_COTE_PX))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception:
        logger.exception("Décodage image impossible pour l'annexe PDF.")
        return None
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _charger_data_uri(doc_id: str) -> str | None:
    from api.documents.crud_document import DocumentServiceError, get_document_content

    try:
        content, _mime, _filename = get_document_content(UUID(str(doc_id)))
    except (DocumentServiceError, ValueError) as exc:
        logger.warning("Photo %s inaccessible pour le PDF : %s", doc_id, exc)
        return None
    except Exception:
        logger.exception("Photo %s : erreur inattendue au téléchargement.", doc_id)
        return None
    return _jpeg_data_uri(content)


def enrichir_photos(lignes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ajoute refs P-n sur les documents image et construit l'annexe groupée.

    Mutates `lignes` (et leurs `documents`) : passer une copie.
    """
    n = 0
    groupes: list[dict[str, Any]] = []
    for ligne in lignes:
        docs = ligne.get("documents") or []
        if not isinstance(docs, list):
            docs = []
            ligne["documents"] = docs
        photos: list[dict[str, Any]] = []
        for doc in docs:
            if not isinstance(doc, dict) or not est_image(doc):
                if isinstance(doc, dict):
                    doc["est_image"] = False
                continue
            n += 1
            ref = f"P-{n}"
            doc["est_image"] = True
            doc["ref_photo"] = ref
            doc_id = str(doc.get("id") or "")
            photo = {
                "id": doc_id,
                "ref": ref,
                "nom": doc.get("nom") or doc.get("nom_fichier") or "Photo",
                "date_document": doc.get("date_document"),
                "data_uri": _charger_data_uri(doc_id) if doc_id else None,
            }
            photos.append(photo)
        ligne["photos"] = photos
        ligne["docs_non_image"] = [
            d for d in docs if isinstance(d, dict) and not d.get("est_image")
        ]
        if photos:
            groupes.append({
                "occurrence_id": ligne.get("id"),
                "code": ligne.get("code"),
                "titre": ligne.get("titre") or "Sans titre",
                "photos": photos,
            })
    return groupes
