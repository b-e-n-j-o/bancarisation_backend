from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from .crud_document import (
    DocumentServiceError,
    create_signed_url,
    delete_document,
    list_documents,
    upload_document,
)


router = APIRouter()


class SignedUrlResponse(BaseModel):
    url: str


@router.get("/projets/{projet_id}/documents")
def list_documents_route(projet_id: UUID) -> list[dict]:
    try:
        return list_documents(projet_id)
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/projets/{projet_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document_route(
    projet_id: UUID,
    file: UploadFile = File(...),
    categorie: str = Form(...),
    date_document: Optional[date] = Form(default=None),
    description: Optional[str] = Form(default=None),
) -> dict:
    try:
        content = await file.read()
        if not content:
            raise DocumentServiceError("Le fichier est vide.")
        return upload_document(
            projet_id=projet_id,
            file_name=file.filename or "document",
            content=content,
            content_type=file.content_type,
            categorie=categorie,
            date_document=date_document,
            description=description,
        )
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document_route(document_id: UUID) -> None:
    try:
        delete_document(document_id)
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/documents/signed-url", response_model=SignedUrlResponse)
def get_signed_url_route(
    bucket_path: str = Query(..., min_length=1),
    download: Optional[str] = Query(default=None),
) -> SignedUrlResponse:
    try:
        url = create_signed_url(bucket_path=bucket_path, download=download)
        return SignedUrlResponse(url=url)
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
