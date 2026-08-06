"""Endpoints HTTP du pipeline multidocs."""

from __future__ import annotations

from typing import Any, List
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status

from ..analyse_jobs import lire_status
from .service import EXTENSIONS_OK, lancer_analyse_multidocs

router = APIRouter()


@router.post("/projets/{projet_id}/analyse-multidocs", status_code=status.HTTP_202_ACCEPTED)
async def start_analyse_multidocs(
    projet_id: UUID,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    replace: bool = False,
) -> dict[str, Any]:
    """Lance le pipeline multidocs (normalisation → triage → plan → extraction).

    Accepte un ou plusieurs fichiers (PDF, XLSX, DOCX, ZIP SIG…).
    """
    status_actuel = lire_status(str(projet_id))
    if status_actuel.get("status") == "running":
        raise HTTPException(status_code=409, detail="Une analyse est déjà en cours.")

    payloads: list[tuple[str, bytes]] = []
    for f in files:
        if not f.filename:
            continue
        ext = "." + f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
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

    background_tasks.add_task(
        lancer_analyse_multidocs,
        str(projet_id),
        fichiers=payloads,
        remplacer_dossier=replace,
        label=", ".join(n for n, _ in payloads[:4]),
    )
    return {
        "status": "started",
        "projet_id": str(projet_id),
        "pipeline": "multidocs",
        "fichiers": [n for n, _ in payloads],
        "message": "Analyse multidocs lancée (normalisation → triage → plan → extraction).",
    }
