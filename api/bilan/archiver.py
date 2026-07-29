"""Archivage du PDF de bilan de suivi dans le bucket documents."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_database_url
from api.documents.crud_document import (
    DocumentServiceError,
    delete_document,
    upload_document,
)

from .pdf import nom_fichier, rendre_pdf
from .suivi import SuiviError, lire_rapport_suivi

CATEGORIE = "suivi"
SOUS_DOSSIER = "bilans-suivi"


def archiver_pdf_suivi(rapport_id: UUID, *, remplacer: bool = True) -> dict[str, Any]:
    """Génère le PDF du snapshot figé et le dépose dans documents …/bilans-suivi/."""
    bilan = lire_rapport_suivi(rapport_id)
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
    display_name = f"Bilan de suivi écologique {annee} — v{version}"
    description = (
        f"Bilan de suivi écologique {annee}, version {version}. "
        "Document généré automatiquement depuis le snapshot archivé."
    )

    try:
        doc = upload_document(
            projet_id=UUID(str(bilan["projet_id"])),
            file_name=filename,
            content=pdf_bytes,
            content_type="application/pdf",
            categorie=CATEGORIE,
            date_document=date(annee, 12, 31),
            description=description,
            nom=display_name,
            sous_dossier=SOUS_DOSSIER,
        )
    except DocumentServiceError as exc:
        raise SuiviError(f"Archivage PDF impossible : {exc}") from exc

    new_doc_id = str(doc.get("id"))
    if not new_doc_id:
        raise SuiviError("Archivage PDF : document sans id.")

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bancarisation.rapport_suivi
                SET document_id = %s::uuid
                WHERE id = %s
                RETURNING id::text, document_id::text, bilan_suivi_id::text
                """,
                (new_doc_id, str(rapport_id)),
            )
            row = cur.fetchone()
            if row and row.get("bilan_suivi_id"):
                cur.execute(
                    """
                    UPDATE bancarisation.bilan_suivi
                    SET document_id = %s::uuid
                    WHERE id = %s::uuid
                    """,
                    (new_doc_id, row["bilan_suivi_id"]),
                )
            conn.commit()

    if row is None:
        try:
            delete_document(UUID(new_doc_id))
        except DocumentServiceError:
            pass
        raise SuiviError("Rapport introuvable lors du rattachement du PDF.")

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
