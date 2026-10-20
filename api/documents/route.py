import logging
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel

from .crud_document import (
    DocumentServiceError,
    create_signed_url_for_document,
    delete_document,
    get_document_content,
    list_documents,
    update_document_classement,
    upload_document,
)


router = APIRouter()
logger = logging.getLogger("documents.route")


class SignedUrlResponse(BaseModel):
    url: str


class ClassementDocument(BaseModel):
    categorie: str
    sous_type: Optional[str] = None


@router.get("/projets/{projet_id}/documents")
def list_documents_route(
    projet_id: UUID,
    occurrence_id: Optional[UUID] = Query(default=None),
    only_global: bool = Query(default=False),
) -> list[dict]:
    try:
        return list_documents(
            projet_id,
            occurrence_id=occurrence_id,
            only_global=only_global,
        )
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/projets/{projet_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document_route(
    projet_id: UUID,
    file: UploadFile = File(...),
    categorie: str = Form(...),
    date_document: Optional[date] = Form(default=None),
    description: Optional[str] = Form(default=None),
    nom: Optional[str] = Form(default=None),
    occurrence_id: Optional[UUID] = Form(default=None),
    sous_type: Optional[str] = Form(default=None),
) -> dict:
    try:
        content = await file.read()
        if not content:
            raise DocumentServiceError("Le fichier est vide.")
        row = upload_document(
            projet_id=projet_id,
            file_name=file.filename or "document",
            content=content,
            content_type=file.content_type,
            categorie=categorie,
            date_document=date_document,
            description=description,
            nom=nom,
            occurrence_id=occurrence_id,
            sous_type=sous_type,
        )
        if categorie == "arrete":
            from api.ocr.ingest_erc.arx.jobs import demarrer_pour_document, est_pdf

            nom_fichier = file.filename or "document"
            if est_pdf(nom_fichier, content, file.content_type):
                try:
                    job = demarrer_pour_document(
                        document_id=row["id"],
                        projet_id=projet_id,
                        contenu=content,
                        nom=nom_fichier,
                        date_document=date_document.isoformat() if date_document else None,
                    )
                    row["analyse_job_id"] = job.get("job_id")
                except Exception:
                    logger.exception("analyse arrêté non démarrée pour %s", row.get("id"))
                    row["analyse_erreur"] = "Le document est enregistré, l'analyse n'a pas pu démarrer."
            else:
                row["analyse_erreur"] = "L'analyse automatique attend un PDF."
        return row
    except DocumentServiceError as exc:
        logger.warning("Dépôt refusé pour le projet %s : %s", projet_id, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/documents/{document_id}/content")
def get_document_content_route(document_id: UUID) -> Response:
    """Proxy fichier via le backend — accessible en local sans URL S3 directe."""
    try:
        content, content_type, filename = get_document_content(document_id)
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Cache-Control": "private, max-age=300",
                "Content-Disposition": f'inline; filename="{filename}"',
            },
        )
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/documents/{document_id}")
def update_document_classement_route(document_id: UUID, body: ClassementDocument) -> dict:
    try:
        return update_document_classement(
            document_id,
            categorie=body.categorie,
            sous_type=body.sous_type,
        )
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_document_route(document_id: UUID) -> None:
    try:
        delete_document(document_id)
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return None


@router.get("/documents/{document_id}/url", response_model=SignedUrlResponse)
def get_signed_url_route(
    document_id: UUID,
    download: Optional[str] = Query(default=None),
) -> SignedUrlResponse:
    try:
        url = create_signed_url_for_document(document_id, download=download)
        return SignedUrlResponse(url=url)
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
