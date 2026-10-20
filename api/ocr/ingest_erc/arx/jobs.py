"""Analyse d'un arrêté déposé : job en arrière-plan, journal LLM, résultat JSON."""
from __future__ import annotations

import json
import logging
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row

from api.db.utilisateur import connect_utilisateur

logger = logging.getLogger("uvicorn.error")

WORK_ROOT = Path(__file__).resolve().parent.parent.parent / "work" / "arx"
_LOCK = threading.Lock()
_ACTIFS: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_dir(job_id: str) -> Path:
    return WORK_ROOT / job_id


def _index_path(document_id: str) -> Path:
    return WORK_ROOT / "index" / f"{document_id}.json"


def lire_index(document_id: str) -> dict[str, Any] | None:
    path = _index_path(document_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _ecrire_index(document_id: str, payload: dict[str, Any]) -> None:
    path = _index_path(document_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def lire_status(job_id: str) -> dict[str, Any] | None:
    path = job_dir(job_id) / "status.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ecrire_status(job_id: str, **champs: Any) -> dict[str, Any]:
    dossier = job_dir(job_id)
    dossier.mkdir(parents=True, exist_ok=True)
    path = dossier / "status.json"
    actuel = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"job_id": job_id}
    actuel.update(champs)
    actuel["updated_at"] = _now()
    path.write_text(json.dumps(actuel, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    document_id = actuel.get("document_id")
    if document_id:
        _ecrire_index(str(document_id), {
            "job_id": job_id,
            "document_id": document_id,
            "status": actuel.get("status"),
            "updated_at": actuel["updated_at"],
        })
    return actuel


def meta_document(document_id: str) -> dict[str, Any] | None:
    with connect_utilisateur(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, projet_id, nom, nom_fichier, categorie, type_mime, date_document
                FROM bancarisation.documents
                WHERE id = %s
                """,
                (str(document_id),),
            )
            return cur.fetchone()


def est_pdf(nom: str, contenu: bytes, content_type: str | None = None) -> bool:
    if contenu[:5] == b"%PDF-":
        return True
    if (nom or "").lower().endswith(".pdf"):
        return True
    return bool(content_type and "pdf" in content_type.lower())


def demarrer_pour_document(
    *,
    document_id: UUID | str,
    projet_id: UUID | str,
    contenu: bytes,
    nom: str,
    date_document: str | None = None,
    arriere_plan: bool = True,
) -> dict[str, Any]:
    """Lance l'analyse si aucune n'est déjà en cours pour ce document."""
    doc_id = str(document_id)
    with _LOCK:
        actuel = lire_index(doc_id)
        if actuel and actuel.get("status") in {"queued", "running"} and str(actuel.get("job_id")) in _ACTIFS:
            statut = lire_status(str(actuel["job_id"]))
            if statut:
                return statut
        job_id = str(uuid4())
        dossier = job_dir(job_id)
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "source.pdf").write_bytes(contenu)
        statut = ecrire_status(
            job_id,
            status="queued",
            etape="en file",
            pipeline="arx_lite",
            document_id=doc_id,
            projet_id=str(projet_id),
            nom=nom,
            date_document=date_document,
            llm=None,
            erreur=None,
        )
    if not arriere_plan:
        logger.info("arx_lite job %s en avant-plan — document %s (%s)", job_id, doc_id, nom)
        _executer(job_id)
        return lire_status(job_id) or statut
    threading.Thread(target=_executer, args=(job_id,), daemon=True).start()
    logger.info("arx_lite job %s lancé — document %s (%s)", job_id, doc_id, nom)
    return statut


def relancer(document_id: str, *, arriere_plan: bool = True) -> dict[str, Any]:
    from api.documents.crud_document import get_document_content

    meta = meta_document(document_id)
    if not meta:
        raise LookupError("Document introuvable.")
    if meta.get("categorie") != "arrete":
        raise ValueError("Seuls les documents classés arrêté peuvent être analysés.")
    contenu, content_type, nom = get_document_content(UUID(str(meta["id"])))
    if not est_pdf(nom, contenu, content_type):
        raise ValueError("L'analyse d'arrêté attend un PDF.")
    date_doc = meta.get("date_document")
    return demarrer_pour_document(
        document_id=meta["id"],
        projet_id=meta["projet_id"],
        contenu=contenu,
        nom=nom,
        date_document=date_doc.isoformat() if hasattr(date_doc, "isoformat") else (str(date_doc) if date_doc else None),
        arriere_plan=arriere_plan,
    )


def vue_document(document_id: str) -> dict[str, Any] | None:
    meta = meta_document(document_id)
    if not meta:
        return None
    index = lire_index(document_id)
    if not index:
        return {
            "document_id": document_id,
            "projet_id": str(meta["projet_id"]),
            "nom": meta.get("nom"),
            "status": "absent",
            "etape": None,
            "llm": None,
            "analyse": None,
            "erreur": None,
        }
    statut = lire_status(str(index["job_id"])) or {}
    analyse = None
    if statut.get("status") == "done" and statut.get("result_path"):
        path = Path(statut["result_path"])
        if path.exists():
            analyse = json.loads(path.read_text(encoding="utf-8"))
    return {
        "document_id": document_id,
        "projet_id": str(meta["projet_id"]),
        "nom": meta.get("nom") or statut.get("nom"),
        "job_id": index.get("job_id"),
        "status": statut.get("status") or index.get("status") or "absent",
        "etape": statut.get("etape"),
        "llm": statut.get("llm"),
        "erreur": (str(statut.get("erreur"))[:800] if statut.get("erreur") else None),
        "analyse": analyse,
        "updated_at": statut.get("updated_at"),
    }


def _importer():
    """Charge arx_lite (lots d'articles en parallèle), pas l'analyse un article par appel."""
    lite = Path(__file__).resolve().parent.parent / "arx_lite"
    lourd = Path(__file__).resolve().parent
    sys.path = [p for p in sys.path if Path(p).resolve() != lourd.resolve()]
    if str(lite) not in sys.path:
        sys.path.insert(0, str(lite))
    for name in list(sys.modules):
        if name == "arx" or name.startswith("arx."):
            del sys.modules[name]
    from arx.config import Settings
    from arx.pipeline import run
    return Settings, run


def _bilan_appels(run_dir: Path, stats: dict) -> dict:
    """Résumé lu dans le journal d'arx_lite, pour l'afficher en fin d'analyse."""
    appels_api = 0
    cache = 0
    tokens = 0
    secondes = 0.0
    journal = run_dir / "llm_calls.jsonl"
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("cached"):
                cache += 1
                continue
            appels_api += 1
            usage = row.get("usage") or {}
            if isinstance(usage, dict):
                tokens += int(usage.get("total_tokens") or 0)
            secondes += float(row.get("seconds") or 0)
    if not appels_api:
        appels_api = int(stats.get("appels_llm_extraction") or 0)
    return {
        "appels_api": appels_api,
        "appels_cache": cache,
        "echecs": 0,
        "tokens_prompt": 0,
        "tokens_completion": 0,
        "tokens_total": tokens,
        "secondes": round(secondes, 1),
        "mode": stats.get("mode"),
    }


def _executer(job_id: str) -> None:
    if job_id in _ACTIFS:
        return
    _ACTIFS.add(job_id)
    try:
        statut = lire_status(job_id) or {}
        pdf = job_dir(job_id) / "source.pdf"
        Settings, run = _importer()
        settings = Settings()
        settings.runs_dir = job_dir(job_id)
        date_doc = statut.get("date_document")
        anchors = {"notification": str(date_doc)[:10]} if date_doc else {}

        def _log(message: str) -> None:
            logger.info("arx_lite %s — %s", job_id, message)
            ecrire_status(job_id, status="running", etape=message, pipeline="arx_lite")

        _log("1/6 démarrage")
        _res, run_dir = run(pdf, settings, anchors=anchors, log=_log)
        bilan = _bilan_appels(run_dir, _res.stats)
        ecrire_status(
            job_id,
            status="done",
            etape="terminé",
            pipeline="arx_lite",
            result_path=str(run_dir / "arrete.json"),
            llm=bilan,
            erreur=None,
        )
        logger.info(
            "arx_lite %s terminé — mode %s, %s appels API, %s tokens",
            job_id,
            bilan.get("mode"),
            bilan.get("appels_api"),
            bilan.get("tokens_total"),
        )
    except (Exception, SystemExit) as exc:
        logger.exception("analyse arrêté %s", job_id)
        ecrire_status(
            job_id,
            status="error",
            etape="erreur",
            erreur=f"{exc}\n{traceback.format_exc()}"[-4000:],
        )
    finally:
        _ACTIFS.discard(job_id)
