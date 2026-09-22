"""HTTP — passe 1 ingest_erc : dépôt de dossier → référentiel à valider."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from .jobs import (
    EXTENSIONS_OK,
    est_bruit_macos,
    creer_job,
    enregistrer_validation,
    lancer_job,
    lire_status,
)

router = APIRouter(prefix="/ocr/ingest-erc", tags=["ingest-erc"])


@router.post("/passe1", status_code=status.HTTP_202_ACCEPTED)
async def demarrer_passe1(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    payloads: list[tuple[str, bytes]] = []
    for f in files:
        if not f.filename:
            continue
        if est_bruit_macos(f.filename):
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in EXTENSIONS_OK:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Format non supporté : {f.filename} "
                    f"(attendu : {', '.join(sorted(EXTENSIONS_OK))})"
                ),
            )
        contenu = await f.read()
        if len(contenu) < 20:
            raise HTTPException(status_code=400, detail=f"Fichier vide : {f.filename}")
        payloads.append((f.filename, contenu))
    if not payloads:
        raise HTTPException(status_code=400, detail="Aucun fichier valide reçu.")

    job_id = creer_job(payloads)
    lancer_job(job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "pipeline": "ingest_erc.passe1",
        "fichiers": [n for n, _ in payloads],
    }


@router.get("/passe1/{job_id}")
def statut_passe1(job_id: str) -> dict[str, Any]:
    s = lire_status(job_id)
    if not s:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")
    # Ne pas renvoyer la stack complète au client si trop longue
    if s.get("erreur") and len(str(s["erreur"])) > 800:
        s = {**s, "erreur": str(s["erreur"])[:800]}
    return s


@router.post("/passe1/{job_id}/valider")
def valider_passe1(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return enregistrer_validation(job_id, payload)
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
