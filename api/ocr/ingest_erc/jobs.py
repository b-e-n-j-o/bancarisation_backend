"""Jobs HTTP de la passe 1 ingest_erc (dépôt de dossier → référentiel à valider)."""
from __future__ import annotations

import json
import re
import threading
import traceback
from collections import Counter
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
    path.write_text(
        json.dumps(actuel, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
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
        "cartes": sortie.get("cartes") or {},
        "classeurs": sortie.get("cartes_classeur") or [],
        "regles": sortie.get("regles") or {},
        "sig": {
            "couches": couches,
            "zones": (sortie.get("sig") or {}).get("zones") or [],
        },
        "referentiel": sortie.get("referentiel") or {},
        "durees_s": sortie.get("durees_s") or {},
        "nb_faits": len(sortie.get("faits") or []),
    }


def creer_job(fichiers: list[tuple[str, bytes]], roles: list[dict[str, str]] | None = None) -> str:
    job_id = str(uuid4())
    dossier = job_dir(job_id) / "dossier"
    dossier.mkdir(parents=True, exist_ok=True)
    noms: list[str] = []
    vus: dict[str, int] = {}
    roles_forces: dict[str, str] = {}
    for i, (nom, contenu) in enumerate(fichiers):
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
        if roles and i < len(roles):
            r = roles[i].get("role") if isinstance(roles[i], dict) else None
            if r:
                roles_forces[sur] = r
    if roles_forces:
        (job_dir(job_id) / "roles.json").write_text(
            json.dumps(roles_forces, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    ecrire_status(
        job_id,
        status="queued",
        etape="depot",
        message="Dossier reçu, analyse en attente…",
        fichiers=noms,
        erreur=None,
        vue=None,
        validation=None,
        passe3=None,
        projet_id=None,
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
        roles_path = job_dir(job_id) / "roles.json"
        roles_forces = None
        if roles_path.exists():
            roles_forces = json.loads(roles_path.read_text(encoding="utf-8"))
        sortie = executer(chemins, on_etape=on_etape, roles_forces=roles_forces)
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


def _dict_reponses(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("reponses") or {}
    if isinstance(raw, list):
        return {
            str(r.get("id")): str(r.get("reponse"))
            for r in raw
            if r.get("id") and r.get("reponse") not in (None, "")
        }
    return {str(k): str(v) for k, v in raw.items() if v not in (None, "")}


def construire_verrouille(sortie: dict, payload: dict[str, Any]):
    """Corrections + réponses BE → référentiel proposé → verrouillé (force si questions encore ouvertes)."""
    from api.ocr.ingest_erc.ingest_erc.corrections import (
        _hydrater_fait,
        _socle_depuis_referentiel,
        appliquer_aux_faits,
        docs_depuis_sortie,
        faits_depuis_corrections,
    )
    from api.ocr.ingest_erc.ingest_erc.modeles import CoucheProfil, Question, ZoneCandidate
    from api.ocr.ingest_erc.ingest_erc.reconcile_ref import reconcilier
    from api.ocr.ingest_erc.ingest_erc.reinjection import faits_depuis_validation, verrouiller

    extra, renames, act_renames, ug_suppr, act_suppr = faits_depuis_corrections(
        payload.get("corrections")
    )
    faits = [_hydrater_fait(f) for f in sortie.get("faits") or []]
    faits += _socle_depuis_referentiel(sortie, faits)
    faits = appliquer_aux_faits(faits, extra, renames, act_renames, ug_suppr, act_suppr)

    questions_prev = []
    for q in (sortie.get("referentiel") or {}).get("questions") or []:
        try:
            questions_prev.append(Question(**q))
        except Exception:  # noqa: BLE001
            continue
    faits += faits_depuis_validation(_dict_reponses(payload), {}, questions_prev)

    zones = [ZoneCandidate(**z) for z in (sortie.get("sig") or {}).get("zones") or []]
    couches = [CoucheProfil(**c) for c in (sortie.get("sig") or {}).get("couches") or []]
    docs = docs_depuis_sortie(sortie)
    ref = reconcilier(faits, zones, couches, docs)
    prev = sortie.get("referentiel") or {}
    if prev.get("anomalies") and not ref.anomalies:
        ref.anomalies = list(prev["anomalies"])
        ref.stats["anomalies"] = len(ref.anomalies)
    verrouille = verrouiller(ref, force=True)
    return ref, verrouille, [f.model_dump() for f in extra]


def _resume_recurrence(r: dict[str, Any] | None) -> str:
    if not r:
        return "—"
    t = r.get("type") or "—"
    bits: list[str] = [str(t)]
    if r.get("ancrage_annee"):
        bits.append(f"dès {r['ancrage_annee']}")
    if r.get("annee_fin"):
        bits.append(f"jusqu'en {r['annee_fin']}")
    if r.get("intervalle_ans"):
        bits.append(f"tous les {r['intervalle_ans']} an(s)")
    if r.get("duree_ans"):
        bits.append(f"{r['duree_ans']} ans")
    if r.get("occurrences_par_an"):
        bits.append(f"{r['occurrences_par_an']}×/an")
    paliers = r.get("paliers") or []
    if paliers:
        bits.append(f"{len(paliers)} palier(s)")
    annees = r.get("annees") or []
    if annees:
        bits.append(", ".join(str(a) for a in annees[:12]))
    src = r.get("regle_source")
    if src:
        bits.append(f"« {str(src)[:90]} »")
    return " · ".join(bits)


def _dump(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if dump:
        return dump(mode="json")
    return dict(obj)


def vue_passe3(res) -> dict[str, Any]:
    rejets = [_dump(r) for r in (res.rejets or [])]
    echs = [_dump(e) for e in (res.echeances or [])]
    occs = [_dump(o) for o in (res.occurrences or [])]
    non_pl = [_dump(e) for e in (res.non_placables or [])]

    annees_par: dict[str, list[int]] = {}
    ht_par: dict[str, float] = {}
    for o in occs:
        cle = str(o.get("echeance_cle") or "")
        if not cle:
            continue
        try:
            annees_par.setdefault(cle, []).append(int(o["annee"]))
        except (KeyError, TypeError, ValueError):
            pass
        if o.get("montant_ht") is not None:
            ht_par[cle] = ht_par.get(cle, 0.0) + float(o["montant_ht"])

    audit = []
    for e in echs:
        cle = str(e.get("id") or "")
        rec = e.get("recurrence") if isinstance(e.get("recurrence"), dict) else {}
        annees = sorted(set(annees_par.get(cle, [])))
        audit.append({
            "id": cle,
            "code": e.get("code_operation"),
            "libelle": e.get("libelle"),
            "ugs": e.get("ug_ids") or [],
            "type": rec.get("type"),
            "regle": _resume_recurrence(rec),
            "ancrage": rec.get("ancrage_annee"),
            "annees": annees,
            "nb_occ": len(annees),
            "montant_ht": ht_par.get(cle),
            "avertissements": (e.get("avertissements") or [])[:4],
        })

    return {
        "stats": res.stats,
        "classeurs": res.classeurs,
        "nb_rejets": len(rejets),
        "rejets_par_motif": dict(Counter(r.get("motif") for r in rejets)),
        "rejets": [
            {"motif": r.get("motif"), "detail": str(r.get("detail") or "")[:220]}
            for r in rejets[:50]
        ],
        "avertissements": list(res.avertissements or [])[:40],
        "non_placables": [
            {
                "code": e.get("code_operation"),
                "libelle": e.get("libelle"),
                "regle": _resume_recurrence(
                    e.get("recurrence") if isinstance(e.get("recurrence"), dict) else {}
                ),
            }
            for e in non_pl
        ],
        "budget_pose": sum((o.get("montant_ht") or 0) for o in occs),
        "budget_non_ventile": sum((l.get("montant_ht") or 0) for l in (res.budget_non_ventile or [])),
        "audit": audit,
    }


def _chemins_dossier(job_id: str) -> list[str]:
    dossier = job_dir(job_id) / "dossier"
    return sorted(
        str(p) for p in dossier.iterdir()
        if p.is_file() and not est_bruit_macos(p.name)
    )


def lancer_passe3(job_id: str):
    """Passe 3 : mêmes cartes/règles que la passe 1 (cache sha256), puis changeset en base."""
    from api.ocr.db.ingestion import connect, ingérer_changeset
    from api.ocr.ingest_erc.ingest_erc import config
    from api.ocr.ingest_erc.ingest_erc.carte import Regles, charger_ou_cartographier
    from api.ocr.ingest_erc.ingest_erc.carte_classeur import charger_carte_cache
    from api.ocr.ingest_erc.ingest_erc.inventaire import inventorier
    from api.ocr.ingest_erc.ingest_erc.modeles import ReferentielVerrouille
    from api.ocr.ingest_erc.ingest_erc.passe3 import executer_passe3
    from api.projets.geometries.persist_depot import persister_depot_sig

    dest = job_dir(job_id)
    cache = config.cache_dir()
    ver_path = dest / "verrouille.json"
    if not ver_path.exists():
        raise ValueError("Référentiel verrouillé introuvable — valider d'abord.")
    ref = ReferentielVerrouille.model_validate_json(ver_path.read_text(encoding="utf-8"))

    def annoncer(msg: str) -> None:
        print(f"📍 {msg}", flush=True)
        ecrire_status(job_id, status="passe3_en_cours", etape="passe3", message=msg, erreur=None)

    annoncer("Analyse du dossier avec vos validations…")

    roles_path = dest / "roles.json"
    roles_forces = None
    if roles_path.exists():
        roles_forces = json.loads(roles_path.read_text(encoding="utf-8"))
    docs = inventorier(_chemins_dossier(job_id), roles_forces)
    cartes = {}
    for d in docs:
        if d.role != "plan_gestion":
            continue
        try:
            c = charger_ou_cartographier(d, cache)
        except Exception as err:  # noqa: BLE001
            print(f"   ⚠️  carte cache/LLM ignorée ({d.nom}) : {err}", flush=True)
            c = None
        if c:
            cartes[d.nom] = c
    cartes_xl = {}
    for d in docs:
        if d.role != "tableur":
            continue
        c = charger_carte_cache(d, cache)
        if c:
            cartes_xl[d.nom] = c
    plan = next((c for c in cartes.values() if c), None)
    regles = Regles.depuis(plan) if plan else Regles.defaut()

    annoncer("Lecture des fiches et construction du calendrier…")
    res = executer_passe3(ref, docs, cartes, regles, cache, cartes_classeur=cartes_xl)
    (dest / "passe3.json").write_text(res.model_dump_json(indent=1), encoding="utf-8")
    vue = vue_passe3(res)
    (dest / "vue_passe3.json").write_text(
        json.dumps(vue, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )

    projet_id = (lire_status(job_id) or {}).get("projet_id")
    ingestion = None
    sig = None
    try:
        sortie = {}
        sp = dest / "sortie_validee.json"
        if not sp.exists():
            sp = dest / "sortie.json"
        if sp.exists():
            sortie = json.loads(sp.read_text(encoding="utf-8"))
        sig_info = sortie.get("sig") or {}
        zones = sig_info.get("zones") or []
        couches = sig_info.get("couches") or []
        docs_sig = [d for d in docs if d.role == "sig"]
        annoncer("Enregistrement du calendrier en base…")
        with connect() as conn:
            ingestion = ingérer_changeset(
                conn, ref, res,
                projet_id=projet_id,
                fichier_nom=next((d.nom for d in docs if d.role == "plan_gestion"), "ingest_erc"),
                replace=bool(projet_id),
            )
            projet_id = ingestion.get("projet_id")
        if projet_id and docs_sig:
            try:
                annoncer("Enregistrement des couches SIG et du cadastre…")
                parties = []
                for doc_sig in docs_sig:
                    parties.append(persister_depot_sig(
                        projet_id=projet_id,
                        ref=ref,
                        sig_zip=doc_sig.chemin,
                        zones=zones,
                        couches=couches,
                        depot_id=getattr(doc_sig, "sha256", None),
                    ))
                sig = parties[0] if len(parties) == 1 else {"depots": parties, "ok": True}
            except Exception as err:  # noqa: BLE001
                traceback.print_exc()
                sig = {"ok": False, "err": str(err)[:300], "bloquant": True}
    except Exception as err:  # noqa: BLE001
        traceback.print_exc()
        ecrire_status(
            job_id,
            status="passe3_faite",
            etape="passe3",
            message="Calendrier extrait, écriture en base incomplète.",
            passe3=vue,
            projet_id=None,
            ingestion={"erreur": str(err)[:400]},
            erreur=str(err)[:400],
        )
        return res

    ecrire_status(
        job_id,
        status="passe3_faite",
        etape="termine",
        message="Calendrier et budget prêts. Relire le projet (brouillon).",
        passe3=vue,
        projet_id=projet_id,
        ingestion=ingestion,
        sig=sig,
        erreur=None,
    )
    return res


def _executer_passe3(job_id: str) -> None:
    try:
        lancer_passe3(job_id)
    except Exception as err:  # noqa: BLE001
        traceback.print_exc()
        ecrire_status(
            job_id,
            status="passe3_erreur",
            etape="erreur",
            message="L'extraction du calendrier a échoué.",
            erreur=str(err)[:500],
        )


def lancer_passe3_job(job_id: str) -> None:
    threading.Thread(target=_executer_passe3, args=(job_id,), daemon=True).start()


def enregistrer_validation(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    actuel = lire_status(job_id)
    if not actuel or actuel.get("status") not in ("done", "validated", "passe3_erreur"):
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
        sortie = json.loads(sortie_path.read_text(encoding="utf-8"))
        ref, verrouille, faits_user = construire_verrouille(sortie, payload)
        sortie["referentiel"] = ref.model_dump()
        sortie["faits_user"] = faits_user
        (dest / "sortie_validee.json").write_text(
            json.dumps(sortie, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        (dest / "verrouille.json").write_text(
            verrouille.model_dump_json(indent=1), encoding="utf-8"
        )
        vue = vue_passe1(sortie)
        (dest / "vue.json").write_text(
            json.dumps(vue, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
    statut = ecrire_status(
        job_id,
        validation=validation,
        vue=vue,
        status="passe3_en_cours",
        etape="passe3",
        message="Analyse du dossier avec vos validations…",
        erreur=None,
    )
    lancer_passe3_job(job_id)
    return statut


def relancer_passe3(job_id: str) -> dict[str, Any]:
    """Reprend un job déjà validé (ex. Le Barp) : verrouille si besoin, relance la passe 3."""
    actuel = lire_status(job_id)
    if not actuel:
        raise ValueError("Analyse introuvable.")
    dest = job_dir(job_id)
    if not (dest / "verrouille.json").exists():
        sortie_path = dest / "sortie_validee.json"
        if not sortie_path.exists():
            sortie_path = dest / "sortie.json"
        if not sortie_path.exists():
            raise ValueError("Sortie passe 1 introuvable.")
        payload = {}
        vp = dest / "validation.json"
        if vp.exists():
            payload = json.loads(vp.read_text(encoding="utf-8"))
        sortie = json.loads(sortie_path.read_text(encoding="utf-8"))
        ref, verrouille, faits_user = construire_verrouille(sortie, payload)
        sortie["referentiel"] = ref.model_dump()
        sortie["faits_user"] = faits_user
        (dest / "sortie_validee.json").write_text(
            json.dumps(sortie, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        (dest / "verrouille.json").write_text(
            verrouille.model_dump_json(indent=1), encoding="utf-8"
        )
        vue = vue_passe1(sortie)
        (dest / "vue.json").write_text(
            json.dumps(vue, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        ecrire_status(job_id, vue=vue)
    statut = ecrire_status(
        job_id,
        status="passe3_en_cours",
        etape="passe3",
        message="Analyse du dossier avec vos validations…",
        erreur=None,
    )
    lancer_passe3_job(job_id)
    return statut

