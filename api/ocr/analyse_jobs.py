"""Suivi des jobs d'analyse PDF par projet."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_SCRIPT_DIR = Path(__file__).resolve().parent
WORK_ROOT = _SCRIPT_DIR / "work"


def work_dir(projet_id: str) -> Path:
    return WORK_ROOT / projet_id


def status_path(projet_id: str) -> Path:
    return work_dir(projet_id) / "analyse_status.json"


def lire_status(projet_id: str) -> dict[str, Any]:
    path = status_path(projet_id)
    if not path.exists():
        return {"status": "idle", "projet_id": projet_id}
    return json.loads(path.read_text(encoding="utf-8"))


def ecrire_status(projet_id: str, **champs: Any) -> dict[str, Any]:
    path = status_path(projet_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    actuel = lire_status(projet_id) if path.exists() else {"projet_id": projet_id}
    actuel.update(champs)
    actuel["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(actuel, ensure_ascii=False, indent=2), encoding="utf-8")
    return actuel


def demarrer(projet_id: str, filename: str) -> dict[str, Any]:
    actuel = lire_status(projet_id)
    if actuel.get("status") == "running":
        raise ValueError("Une analyse est déjà en cours pour ce projet.")
    return ecrire_status(
        projet_id,
        status="running",
        etape="ocr",
        fichier=filename,
        erreur=None,
        recap=None,
        started_at=datetime.now(timezone.utc).isoformat(),
    )


def avancer(projet_id: str, etape: str, detail: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "running", "etape": etape}
    if detail:
        payload["detail"] = detail
    return ecrire_status(projet_id, **payload)


def terminer(projet_id: str, recap: dict[str, Any]) -> dict[str, Any]:
    return ecrire_status(
        projet_id,
        status="done",
        etape="termine",
        erreur=None,
        recap=recap,
    )


def echouer(projet_id: str, erreur: str) -> dict[str, Any]:
    return ecrire_status(
        projet_id,
        status="error",
        etape="erreur",
        erreur=erreur,
    )
