"""manifest.json : empreintes des fichiers, pas du manifeste lui-même."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from api.export.contexte import ExportContexte, FichierExport

_PARIS = ZoneInfo("Europe/Paris")


def version_application() -> str:
    env = os.environ.get("APP_VERSION", "").strip()
    if env:
        return env
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
        return out.strip() or "dev"
    except (OSError, subprocess.SubprocessError):
        return "dev"


def empreinte(path: Path) -> tuple[str, int]:
    hachage = hashlib.sha256()
    taille = 0
    with path.open("rb") as fichier:
        for bloc in iter(lambda: fichier.read(1024 * 1024), b""):
            hachage.update(bloc)
            taille += len(bloc)
    return hachage.hexdigest(), taille


def construire(
    ctx: ExportContexte,
    fichiers: list[FichierExport],
    modules: dict[str, dict[str, Any]],
    *,
    email: str | None,
    organisation_nom: str,
    projet_nom: str,
    reference: str | None,
) -> dict[str, Any]:
    entrees = []
    for fic in fichiers:
        sha, taille = empreinte(fic.chemin_local)
        entrees.append(
            {
                "chemin": fic.chemin_archive,
                "module": fic.module,
                "sha256": sha,
                "taille": taille,
                "nb_elements": fic.nb_elements,
                "description": fic.description,
            }
        )
    return {
        "format": "kererc-export-projet",
        "version_format": "1.1",
        "genere_le": datetime.now(_PARIS).isoformat(timespec="seconds"),
        "genere_par": {"user_id": str(ctx.user_id), "email": email},
        "organisation": {"id": str(ctx.organisation_id), "nom": organisation_nom},
        "projet": {
            "id": str(ctx.projet_id),
            "nom": projet_nom,
            "reference_interne": reference,
        },
        "options": ctx.options.pour_json(),
        "version_application": version_application(),
        "fichiers": entrees,
        "modules": modules,
        "avertissements": list(ctx.avertissements),
    }


def ecrire(dossier: Path, manifest: dict[str, Any]) -> Path:
    cible = dossier / "manifest.json"
    cible.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return cible
