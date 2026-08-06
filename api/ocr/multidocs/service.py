"""Orchestration API : dossier BE → pipeline multidocs (run.py).

Remplace le chemin monodoc `analyse_service` (PDF → ancien pipeline).
Statut partagé via `analyse_jobs` pour que l'UI poll `/analyse-status`.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from api.db.env import load_db_env

from ..analyse_jobs import avancer, demarrer, echouer, ecrire_status, terminer, work_dir
from ..mistral_client import DEFAULT_MODEL, PRIX, PRIX_DEFAUT, Compteur
from .etape1_normalisation import FORMATS_SUPPORTES, normaliser_dossier
from .etape2_triage import cartographier
from .etape3_planification import planifier, resume
from .etape4_extraction import executer
from .journal import JournalPipeline
from .utils.plan import ZoneSigContexte

load_db_env()

log = logging.getLogger("analyse.multidocs")

EXTENSIONS_OK = {ext.lower() for ext in FORMATS_SUPPORTES}


def dossier_source(projet_id: str) -> Path:
    return work_dir(projet_id) / "dossier"


def sortie_multidocs(projet_id: str) -> Path:
    return work_dir(projet_id) / "multidocs"


def ocr_pages_prod(chemin: Path) -> list[str]:
    """OCR via le client prod (`ocr_mistral`), page par page pour le cache multidocs."""
    from ..ocr_mistral import pdf_vers_markdown

    import tempfile

    with tempfile.TemporaryDirectory(prefix="ocr_md_") as tmp:
        out = Path(tmp)
        _, n = pdf_vers_markdown(chemin.read_bytes(), chemin.name, out)
        pages: list[str] = []
        for i in range(1, n + 1):
            p = out / f"page_{i:02d}.md"
            pages.append(p.read_text(encoding="utf-8") if p.exists() else "")
        return pages


def charger_zones_sig(projet_id: str) -> list[ZoneSigContexte]:
    """UG confirmées en base → référentiel pour le LLM (ug_id ↔ libellé / fichier)."""
    try:
        from api.ocr.domain.ug_ids import normalize_ug_id
        from api.projets.geometries.crud import lister_geometries_ug
    except Exception:  # noqa: BLE001
        return []

    try:
        fc = lister_geometries_ug(UUID(projet_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("zones_sig indisponibles pour %s : %s", projet_id, exc)
        return []

    # Une entrée par ug_id (plusieurs parcelles = même UG)
    par_ug: dict[str, ZoneSigContexte] = {}
    for feat in fc.get("features") or []:
        props = feat.get("properties") or {}
        if props.get("couche") == "emprise":
            continue
        ug_raw = props.get("ug_id")
        ug = normalize_ug_id(str(ug_raw) if ug_raw is not None else None)
        if not ug:
            continue

        libelle = (props.get("libelle") or props.get("nom") or "").strip()
        source = (props.get("source_fichier") or "").strip() or None
        nom_source = libelle or source or ug
        cat = props.get("categorie_erc") or props.get("categorie") or "autre"
        surface = props.get("surface_ha")
        try:
            surface_f = float(surface) if surface is not None else None
        except (TypeError, ValueError):
            surface_f = None

        if ug not in par_ug:
            par_ug[ug] = ZoneSigContexte(
                ug_id=ug,
                nom_source=str(nom_source),
                categorie=str(cat),
                surface_ha=surface_f,
                cible=(props.get("cible") or None),
                libelle=libelle or str(nom_source),
                source_fichier=source,
            )
        else:
            z = par_ug[ug]
            if surface_f is not None:
                z.surface_ha = (z.surface_ha or 0.0) + surface_f
            if not z.source_fichier and source:
                z.source_fichier = source
            if not z.libelle and libelle:
                z.libelle = libelle

    # Compléter depuis le résumé `ugs` si features vides mais ugs présents
    for u in fc.get("ugs") or []:
        ug = normalize_ug_id(str(u.get("id") or u.get("ug_id") or ""))
        if not ug or ug in par_ug:
            continue
        libelle = (u.get("libelle") or ug).strip()
        par_ug[ug] = ZoneSigContexte(
            ug_id=ug,
            nom_source=libelle,
            categorie="autre",
            libelle=libelle,
        )

    zones = sorted(par_ug.values(), key=lambda z: z.ug_id)
    if zones:
        log.info(
            "Référentiel UG projet %s : %s",
            projet_id,
            ", ".join(f"{z.ug_id}={z.libelle or z.nom_source}" for z in zones),
        )
    return zones


def enregistrer_fichiers(
    projet_id: str,
    fichiers: list[tuple[str, bytes]],
    *,
    remplacer: bool = False,
) -> list[str]:
    """Écrit les fichiers dans work/{id}/dossier/. Retourne les noms retenus."""
    racine = dossier_source(projet_id)
    if remplacer and racine.exists():
        shutil.rmtree(racine)
    racine.mkdir(parents=True, exist_ok=True)

    retenus: list[str] = []
    for nom, contenu in fichiers:
        safe = Path(nom).name
        if not safe or safe.startswith("."):
            continue
        # Extension toujours en minuscules → .PDF / .Pdf traités comme .pdf
        stem, ext = Path(safe).stem, Path(safe).suffix.lower()
        if ext not in EXTENSIONS_OK:
            continue
        if len(contenu) < 20:
            continue
        safe_disk = f"{stem}{ext}"
        (racine / safe_disk).write_bytes(contenu)
        retenus.append(safe_disk)
    return retenus


def _suivi_fichiers_init(racine: Path) -> list[dict[str, Any]]:
    fichiers: list[dict[str, Any]] = []
    for p in sorted(racine.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        fichiers.append({
            "nom": p.name,
            "statut": "pending",  # pending | running | done | error | skip
            "pages": None,
            "format": p.suffix.lstrip(".").lower() or None,
        })
    return fichiers


def _suivi_apres_corpus(corpus) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in corpus.documents:
        pages = d.meta.get("nb_pages")
        if pages is None and d.format == "pdf":
            pages = len(d.blocs)
        out.append({
            "nom": d.nom_fichier,
            "doc_id": d.doc_id,
            "statut": "done",
            "pages": pages,
            "format": d.format,
            "blocs": len(d.blocs),
        })
    return out


def _marquer_fichier(
    fichiers: list[dict[str, Any]],
    *,
    nom: str | None = None,
    doc_id: str | None = None,
    statut: str,
) -> list[dict[str, Any]]:
    maj = []
    for f in fichiers:
        item = dict(f)
        match = (nom and item.get("nom") == nom) or (doc_id and item.get("doc_id") == doc_id)
        if match:
            item["statut"] = statut
        elif item.get("statut") == "running":
            item["statut"] = "done"
        maj.append(item)
    return maj


def lancer_analyse_multidocs(
    projet_id: str,
    *,
    fichiers: list[tuple[str, bytes]] | None = None,
    remplacer_dossier: bool = False,
    label: str | None = None,
) -> None:
    """Background task — pipeline run.py jusqu'à extraction (claims.jsonl)."""
    load_db_env()
    t0 = time.perf_counter()
    journal = JournalPipeline(projet_id)
    journal.activer()
    compteur = Compteur(DEFAULT_MODEL, *PRIX.get(DEFAULT_MODEL, PRIX_DEFAUT))
    fichiers_suivi: list[dict[str, Any]] = []

    def _duree_analyse() -> float:
        return time.perf_counter() - t0

    def _log_duree(ok: bool) -> None:
        duree = _duree_analyse()
        statut = "terminée" if ok else "échouée"
        msg = f"⏱ Analyse multidocs {statut} en {duree:.1f}s — projet {projet_id}"
        print(msg, flush=True)
        log.info(msg)

    try:
        noms = [n for n, _ in (fichiers or [])]
        if fichiers:
            retenus = enregistrer_fichiers(
                projet_id, fichiers, remplacer=remplacer_dossier,
            )
            if not retenus and remplacer_dossier:
                raise ValueError(
                    "Aucun fichier supporté (PDF, XLSX, DOCX, ZIP SIG…)."
                )

        racine = dossier_source(projet_id)
        if not racine.exists() or not any(racine.rglob("*")):
            raise ValueError("Dossier source vide — déposez des documents avant l'analyse.")

        fichiers_suivi = _suivi_fichiers_init(racine)
        demarrer(
            projet_id,
            label or (", ".join(noms[:3]) if noms else "dossier BE"),
            fichiers=fichiers_suivi,
        )

        sortie = sortie_multidocs(projet_id)
        if sortie.exists():
            shutil.rmtree(sortie)
        sortie.mkdir(parents=True, exist_ok=True)
        debug_dir = sortie / "debug"
        cache = work_dir(projet_id) / "cache_ocr"

        # --- 1. normalisation ------------------------------------------------
        journal.debut(1, "Normalisation", "etape1 + utils/pdf → OCR Mistral")
        if fichiers_suivi:
            fichiers_suivi = [
                {**f, "statut": "running" if i == 0 else f["statut"]}
                for i, f in enumerate(fichiers_suivi)
            ]
        avancer(
            projet_id, "normalisation",
            "Lecture / OCR des documents…",
            message_user="Lecture des documents…",
            fichiers=fichiers_suivi,
            progression=5,
        )
        corpus, laisses = normaliser_dossier(
            racine,
            cache_dir=cache,
            ocr_pages=ocr_pages_prod,
        )
        (sortie / "corpus.json").write_text(
            corpus.model_dump_json(indent=2), encoding="utf-8",
        )
        n_docs = len(corpus.documents)
        n_blocs = sum(len(d.blocs) for d in corpus.documents)
        if not corpus.documents:
            journal.fin("aucun document normalisé", ok=False)
            raise ValueError(
                "Aucun document normalisé. "
                + (f"Ignorés : {', '.join(laisses)}" if laisses else "")
            )
        fichiers_suivi = _suivi_apres_corpus(corpus)
        pages_totales = sum(f["pages"] or 0 for f in fichiers_suivi if f.get("pages"))
        detail_ocr = (
            f"{journal.ocr_appels} API"
            + (f", {journal.ocr_cache_hits} cache" if journal.ocr_cache_hits else "")
        )
        # Volume texte OCR (c'est CE texte qui ira aux LLM, pas le PDF binaire)
        for d in corpus.documents:
            n_car = sum(len(b.texte or "") for b in d.blocs)
            print(
                f"📄 [OCR→LLM] « {d.nom_fichier} » — {len(d.blocs)} bloc(s), "
                f"{n_car:,} car. texte OCR "
                f"(~{max(1, n_car // 4):,} tokens estimés)",
                flush=True,
            )
        journal.fin(
            f"{n_docs} doc(s), {n_blocs} bloc(s)"
            + (f" · ignorés : {len(laisses)}" if laisses else ""),
            detail_appels=detail_ocr,
        )

        # --- 2. triage -------------------------------------------------------
        journal.debut(2, "Triage", "etape2_triage.cartographier")
        msg_triage = (
            f"Repérage des documents ({pages_totales} pages)…"
            if pages_totales
            else "Repérage des documents…"
        )
        avancer(
            projet_id, "triage",
            "Cartographie des rôles documentaires…",
            message_user=msg_triage,
            fichiers=fichiers_suivi,
            pages_totales=pages_totales or None,
            progression=30,
        )
        llm_avant = compteur.appels
        carte = cartographier(corpus, debug_dir=debug_dir, compteur=compteur)
        llm_triage = compteur.appels - llm_avant
        (sortie / "carte.json").write_text(
            carte.model_dump_json(indent=2), encoding="utf-8",
        )
        roles = ", ".join(sorted({s.role.value for s in carte.segments})) or "—"
        journal.fin(
            f"{len(carte.segments)} segment(s) · rôles : {roles} · "
            f"{len(carte.parametres)} param. · {len(carte.signaux)} signal(aux)",
            appels_llm=llm_triage,
            detail_appels=f"{llm_triage} chat (retries inclus)",
            ok=bool(carte.segments),
        )

        # --- 3. plan ---------------------------------------------------------
        journal.debut(3, "Plan", "etape3_planification (déterministe)")
        avancer(
            projet_id, "plan",
            "Planification des extracteurs…",
            message_user="Préparation de l'analyse…",
            fichiers=fichiers_suivi,
            pages_totales=pages_totales or None,
            progression=45,
        )
        plan = planifier(carte, corpus)
        (sortie / "plan.json").write_text(
            plan.model_dump_json(indent=2), encoding="utf-8",
        )
        extracteurs = ", ".join(sorted({j.extracteur for j in plan.jobs})) or "—"
        journal.fin(
            f"{len(plan.jobs)} job(s) · {extracteurs}",
            appels_llm=0,
            detail_appels="0 (pas de LLM)",
            ok=True,
        )
        log.info("Plan — projet %s\n%s", projet_id, resume(plan))

        # --- 4. extraction ---------------------------------------------------
        journal.debut(4, "Extraction", "etape4 → extracteurs (plan_gestion…)")
        zones = charger_zones_sig(projet_id)
        n_jobs = len(plan.jobs)
        jobs_faits = 0

        def _on_job(job, ext, doc) -> None:
            nonlocal fichiers_suivi, jobs_faits
            pages = doc.meta.get("nb_pages") or (
                len(doc.blocs) if doc.format == "pdf" else None
            )
            n_pages = pages_totales or pages
            fichiers_suivi = _marquer_fichier(
                fichiers_suivi, doc_id=doc.doc_id, nom=doc.nom_fichier, statut="running",
            )
            if n_pages:
                msg = f"Analyse des {n_pages} pages…"
            else:
                msg = f"Analyse de « {doc.nom_fichier} »…"
            avancer(
                projet_id, "extraction",
                f"Job {jobs_faits + 1}/{n_jobs} · {ext.nom}",
                message_user=msg,
                fichiers=fichiers_suivi,
                pages_totales=n_pages or None,
                fichier=doc.nom_fichier,
                progression=50 + int(45 * jobs_faits / max(n_jobs, 1)),
            )
            jobs_faits += 1

        llm_avant = compteur.appels
        claims = executer(
            plan, corpus, carte, sortie, debug_dir,
            zones_sig=zones,
            compteur=compteur,
            on_job=_on_job,
        )
        llm_ext = compteur.appels - llm_avant
        fichiers_suivi = [
            {**f, "statut": "done" if f.get("statut") != "skip" else "skip"}
            for f in fichiers_suivi
        ]

        par_kind: dict[str, int] = {}
        for c in claims:
            par_kind[c.kind.value] = par_kind.get(c.kind.value, 0) + 1
        kinds = ", ".join(f"{k}={n}" for k, n in sorted(par_kind.items())) or "aucun"
        journal.fin(
            f"{len(claims)} claim(s) · {kinds}",
            appels_llm=llm_ext,
            detail_appels=f"{llm_ext} chat",
            ok=True,
        )

        # --- 5. Semoir + ingestion (0 LLM) ------------------------------------
        n_actions = par_kind.get("action", 0)
        n_echeances = par_kind.get("echeance_regle", 0)
        n_occurrences = 0
        n_non_placables = 0
        horizon = 0
        avert_semoir: list[str] = []

        claims_path = sortie / "claims.jsonl"
        journal.debut(5, "Calendrier", "claims → semoir → ingestion")
        avancer(
            projet_id, "occurrences",
            "Génération du calendrier…",
            message_user="Construction du calendrier…",
            fichiers=fichiers_suivi,
            pages_totales=pages_totales or None,
            progression=92,
        )
        if n_echeances > 0 and claims_path.is_file():
            try:
                from ..claims_vers_calendrier import executer as semer_claims

                ingest_recap = semer_claims(
                    projet_id,
                    claims_path,
                    replace=True,
                    fichier_nom=label or (noms[0] if noms else "dossier BE"),
                )
                n_occurrences = int(ingest_recap.get("occurrences_inserees") or 0)
                n_non_placables = int(ingest_recap.get("nb_non_placables") or 0)
                # horizon stocké dans occurrences.json
                occ_meta = sortie / "occurrences.json"
                if occ_meta.exists():
                    try:
                        horizon = int(
                            json.loads(occ_meta.read_text(encoding="utf-8")).get("annee_fin") or 0
                        )
                    except Exception:  # noqa: BLE001
                        horizon = 0
                journal.fin(
                    f"{n_occurrences} occurrence(s)"
                    + (f" · {n_non_placables} non placable(s)" if n_non_placables else ""),
                    appels_llm=0,
                    detail_appels="0 (déterministe)",
                    ok=True,
                )
            except Exception as err:  # noqa: BLE001
                avert_semoir.append(f"Semoir / ingestion : {err}")
                journal.fin(f"échec : {err}", ok=False)
                print(f"  ⚠️  Semoir échoué (claims conservés) : {err}", flush=True)
        else:
            journal.fin("aucune échéance — semoir sauté", ok=True)

        recap: dict[str, Any] = {
            "pipeline": "multidocs",
            "docs": n_docs,
            "blocs": n_blocs,
            "segments": len(carte.segments),
            "jobs": len(plan.jobs),
            "claims": len(claims),
            "par_kind": par_kind,
            "zones_sig": len(zones),
            "laisses": laisses,
            "non_couvert": plan.non_couvert,
            "avertissements": list(plan.avertissements) + avert_semoir,
            "sortie": str(sortie),
            "journal": journal.to_dict(),
            "llm_appels": compteur.appels,
            "ocr_appels": journal.ocr_appels,
            "cout_total_usd": round(compteur.cout, 4),
            "stats": {
                "actions": n_actions,
                "echeances": n_echeances,
                "occurrences": n_occurrences,
                "non_placables": n_non_placables,
                "horizon": horizon,
            },
        }
        (sortie / "journal.json").write_text(
            json.dumps(journal.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(journal.rapport(), flush=True)
        print(compteur.rapport(), flush=True)
        _log_duree(True)
        msg_fin = (
            f"Analyse terminée — {n_actions} mesure(s), {n_echeances} échéance(s), "
            f"{n_occurrences} occurrence(s)"
            if n_occurrences
            else "Analyse terminée"
        )
        terminer(
            projet_id,
            recap,
            fichiers=fichiers_suivi,
            message_user=msg_fin,
            progression=100,
            pages_totales=pages_totales or None,
        )
        log.info(
            "Multidocs terminé — %d claim(s), %d occ, %d LLM, %d OCR — projet %s",
            len(claims), n_occurrences, compteur.appels, journal.ocr_appels, projet_id,
        )
    except Exception as exc:
        journal.abandonner(str(exc))
        if journal.lignes:
            print(journal.rapport(), flush=True)
            try:
                sortie = sortie_multidocs(projet_id)
                sortie.mkdir(parents=True, exist_ok=True)
                (sortie / "journal.json").write_text(
                    json.dumps(journal.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:  # noqa: BLE001
                pass
        fichiers_err = [
            {**f, "statut": "error" if f.get("statut") == "running" else f.get("statut", "pending")}
            for f in fichiers_suivi
        ]
        try:
            ecrire_status(
                projet_id,
                fichiers=fichiers_err,
                message_user=str(exc)[:200],
            )
        except Exception:  # noqa: BLE001
            pass
        _log_duree(False)
        log.exception("Multidocs échoué — projet %s", projet_id)
        echouer(projet_id, str(exc))
