"""Archivage du PDF de bilan dans le bucket documents (dossier bilans/)."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.rows import dict_row
import psycopg

from api.db.env import get_database_url
from api.documents.crud_document import (
    DocumentServiceError,
    delete_document,
    upload_document,
)

from .bilan import BilanError, lire_bilan
from .pdf import nom_fichier, rendre_pdf

CATEGORIE_BILAN = "financier"
SOUS_DOSSIER_BILANS = "bilans"


def archiver_pdf_bilan(rapport_id: UUID, *, remplacer: bool = True) -> dict[str, Any]:
    """Génère le PDF du snapshot, l'upload dans documents-projet/{projet}/bilans/
    et rattache `document_id` au rapport_bilan.

    Le PDF est un rendu du snapshot figé — pas un recalcul.
    """
    bilan = lire_bilan(rapport_id)
    old_doc_id = bilan.get("document_id")
    if old_doc_id and not remplacer:
        return {
            "rapport_id": bilan["id"],
            "document_id": old_doc_id,
            "cree": False,
            "document": None,
        }

    pdf_bytes = rendre_pdf(bilan)
    filename = nom_fichier(bilan)
    annee = int(bilan["annee"])
    version = int(bilan["version"])
    display_name = f"Bilan financier {annee} — v{version}"
    description = (
        f"Bilan financier annuel {annee}, version {version}. "
        "Document généré automatiquement depuis le snapshot archivé."
    )

    try:
        doc = upload_document(
            projet_id=UUID(str(bilan["projet_id"])),
            file_name=filename,
            content=pdf_bytes,
            content_type="application/pdf",
            categorie=CATEGORIE_BILAN,
            date_document=date(annee, 12, 31),
            description=description,
            nom=display_name,
            sous_dossier=SOUS_DOSSIER_BILANS,
        )
    except DocumentServiceError as exc:
        raise BilanError(f"Archivage PDF impossible : {exc}") from exc

    new_doc_id = str(doc.get("id"))
    if not new_doc_id:
        raise BilanError("Archivage PDF : document sans id.")

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bancarisation.rapport_bilan
                SET document_id = %s::uuid
                WHERE id = %s
                RETURNING id::text, document_id::text
                """,
                (new_doc_id, str(rapport_id)),
            )
            row = cur.fetchone()
    if row is None:
        # Rollback logique : supprimer le doc orphelin
        try:
            delete_document(UUID(new_doc_id))
        except DocumentServiceError:
            pass
        raise BilanError("Bilan introuvable lors du rattachement du PDF.")

    if old_doc_id and remplacer and str(old_doc_id) != new_doc_id:
        try:
            delete_document(UUID(str(old_doc_id)))
        except DocumentServiceError:
            pass

    return {
        "rapport_id": row["id"],
        "document_id": row["document_id"],
        "cree": True,
        "document": doc,
    }
