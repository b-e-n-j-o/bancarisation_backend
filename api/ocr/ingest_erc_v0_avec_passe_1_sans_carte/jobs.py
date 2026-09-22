"""Jobs HTTP de la passe 1 ingest_erc (dépôt de dossier → référentiel à valider)."""
from __future__ import annotations

import json
import re
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

WORK_ROOT = Path(__file__).resolve().parent.parent / "work" / "ingest_erc"
EXTENSIONS_OK = {".pdf", ".xlsx", ".xlsm", ".zip", ".gpkg", ".geojson"}


def est_bruit_macos(nom: str) -> bool:
    n = Path(nom).name
    if n.startswith(".") or n.startswith("._"):
        return True
    if "__macosx" in nom.lower().replace("\\", "/"):
        return True
    return n in {"Thumbs.db", "desktop.ini"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_dir(job_id: str) -> Path:
    return WORK_ROOT / job_id


def status_path(job_id: str) -> Path:
    return job_dir(job_id) / "status.json"


def lire_status(job_id: str) -> dict[str, Any] | None:
    path = status_path(job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ecrire_status(job_id: str, **champs: Any) -> dict[str, Any]:
    path = status_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    actuel = lire_status(job_id) if path.exists() else {"job_id": job_id}
    actuel.update(champs)
    actuel["updated_at"] = _now()
    path.write_text(json.dumps(actuel, ensure_ascii=False, indent=2), encoding="utf-8")
    return actuel


def _nom_sur(nom: str) -> str:
    base = Path(nom).name
    base = re.sub(r"[^\w.\- àâäéèêëïîôöùûüçÀÂÄÉÈÊËÏÎÔÖÙÛÜÇ()]+", "_", base).strip("._")
    return base or "fichier"


def vue_passe1(sortie: dict) -> dict[str, Any]:
    """Payload UI : sans dump de colonnes SIG ni faits bruts."""
    couches = []
    for c in (sortie.get("sig") or {}).get("couches") or []:
        couches.append({
            "nom": c.get("nom"),
            "fichier": c.get("fichier"),
            "geom_type": c.get("geom_type"),
            "nb_entites": c.get("nb_entites"),
            "epsg": c.get("epsg"),
            "surface_ha": c.get("surface_ha"),
            "avertissements": c.get("avertissements") or [],
        })
    return {
        "config": sortie.get("config"),
        "documents": sortie.get("documents") or [],
        "selection_pdf": sortie.get("selection_pdf") or {},
        "sig": {
            "couches": couches,
            "zones": (sortie.get("sig") or {}).get("zones") or [],
        },
        "referentiel": sortie.get("referentiel") or {},
        "durees_s": sortie.get("durees_s") or {},
        "nb_faits": len(sortie.get("faits") or []),
    }


def creer_job(fichiers: list[tuple[str, bytes]]) -> str:
    job_id = str(uuid4())
    dossier = job_dir(job_id) / "dossier"
    dossier.mkdir(parents=True, exist_ok=True)
    noms: list[str] = []
    vus: dict[str, int] = {}
    for nom, contenu in fichiers:
        if est_bruit_macos(nom):
            continue
        ext = Path(nom).suffix.lower()
        if ext not in EXTENSIONS_OK:
            continue
        sur = _nom_sur(nom)
        n = vus.get(sur, 0)
        vus[sur] = n + 1
        if n:
            stem, ext = Path(sur).stem, Path(sur).suffix
            sur = f"{stem}_{n}{ext}"
        (dossier / sur).write_bytes(contenu)
        noms.append(sur)
    ecrire_status(
        job_id,
        status="queued",
        etape="depot",
        message="Dossier reçu, analyse en attente…",
        fichiers=noms,
        erreur=None,
        vue=None,
        validation=None,
        started_at=None,
    )
    return job_id


def _executer_job(job_id: str) -> None:
    from api.ocr.ingest_erc.ingest_erc.passe1 import executer

    ecrire_status(
        job_id,
        status="running",
        etape="inventaire",
        message="Lecture et OCR des documents…",
        started_at=_now(),
    )
    dossier = job_dir(job_id) / "dossier"
    chemins = sorted(
        str(p) for p in dossier.iterdir()
        if p.is_file() and not est_bruit_macos(p.name)
    )

    def on_etape(cle: str, msg: str) -> None:
        ecrire_status(job_id, status="running", etape=cle, message=msg)

    try:
        sortie = executer(chemins, on_etape=on_etape)
        (job_dir(job_id) / "sortie.json").write_text(
            json.dumps(sortie, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        vue = vue_passe1(sortie)
        (job_dir(job_id) / "vue.json").write_text(
            json.dumps(vue, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        ecrire_status(
            job_id,
            status="done",
            etape="termine",
            message="Référentiel prêt à valider.",
            vue=vue,
            erreur=None,
        )
    except Exception as err:  # noqa: BLE001
        traceback.print_exc()
        msg = str(err)
        if "tier_not_allowed" in msg or "subscription tier" in msg.lower():
            msg = (
                "Le modèle LLM n'est pas inclus dans le palier de la clé Mistral. "
                "GLM 5.2 nécessite MISTRAL_API_KEY_BEN (smoke test) ; "
                "sinon le pipeline replie sur mistral-medium-3-5."
            )
        elif "._" in msg and "octet-stream" in msg:
            msg = "Un fichier macOS (._) a été envoyé à l'OCR. Relancez le dépôt (ils sont maintenant ignorés)."
        ecrire_status(
            job_id,
            status="error",
            etape="erreur",
            message="L'analyse a échoué.",
            erreur=msg[:500],
        )


def lancer_job(job_id: str) -> None:
    threading.Thread(target=_executer_job, args=(job_id,), daemon=True).start()


def enregistrer_validation(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    actuel = lire_status(job_id)
    if not actuel or actuel.get("status") != "done":
        raise ValueError("Aucune analyse terminée pour ce dossier.")
    validation = {**payload, "origine": "user", "validated_at": _now()}
    dest = job_dir(job_id)
    (dest / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    vue = actuel.get("vue")
    sortie_path = dest / "sortie.json"
    if sortie_path.exists():
        from api.ocr.ingest_erc.ingest_erc.corrections import appliquer_et_reconcilier
        sortie = json.loads(sortie_path.read_text(encoding="utf-8"))
        ref, faits_user = appliquer_et_reconcilier(sortie, payload.get("corrections"))
        sortie["referentiel"] = ref
        sortie["faits_user"] = faits_user
        (dest / "sortie_validee.json").write_text(
            json.dumps(sortie, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        vue = vue_passe1(sortie)
        (dest / "vue.json").write_text(
            json.dumps(vue, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
    return ecrire_status(job_id, validation=validation, vue=vue, status="validated")
