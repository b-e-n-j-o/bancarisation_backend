"""Téléversement, analyse, puis import confirmé."""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from psycopg.rows import dict_row
from pydantic import BaseModel

from api.db.supabase import get_supabase_admin
from api.db.utilisateur import connect_utilisateur
from api.export.contexte import connect_backend
from api.export.sqlutil import une
from api.reimport.analyse import analyser_archive, extraire_zip, ouvrir_zip
from api.reimport.chargeur import importer, marquer
from api.reimport.modele.listes import ROLES
from auth.deps import get_claims

logger = logging.getLogger("api.reimport.router")
router = APIRouter()
BUCKET = "imports-projet"


def _claims_conn(claims):
    return connect_utilisateur(claims, row_factory=dict_row)


@router.get("/modeles/import-projet.xlsx")
def telecharger_modele():
    from api.reimport.modele.generateur import generer_classeur
    corps = generer_classeur(_roles_referentiel())
    return Response(
        corps,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=projet.xlsx"},
    )


class ParametresAnalyse(BaseModel):
    sig: dict[str, str] | None = None
    figer_baseline: bool = True


@router.post("/organisations/{organisation_id}/imports", status_code=status.HTTP_202_ACCEPTED)
async def televerser(organisation_id: UUID, taches: BackgroundTasks, fichier: UploadFile = File(...)):
    claims = get_claims()
    with _claims_conn(claims) as conn:
        droit = une(conn, "select bancarisation.peut_importer_dans_organisation(%s) as ok", (str(organisation_id),))
    if not droit or not droit["ok"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Seul un admin de l'organisation peut importer un projet.")
    contenu = await fichier.read()
    if not contenu:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Fichier vide.")
    if (fichier.filename or "").lower().endswith(".xlsm"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Les fichiers .xlsm ne sont pas acceptés.")
    source, contenu = _preparer(contenu, fichier.filename or "")
    sha = hashlib.sha256(contenu).hexdigest()
    with _claims_conn(claims) as conn:
        cree = une(
            conn,
            """
            insert into bancarisation.import_projet
              (organisation_id, demande_par, sha256, statut, source)
            values (%s, %s, %s, 'televerse', %s)
            returning id::text
            """,
            (str(organisation_id), str(claims["sub"]), sha, source),
        )
    if not cree:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Import refusé.")
    import_id = cree["id"]
    chemin = f"{organisation_id}/{import_id}.zip"
    get_supabase_admin().storage.from_(BUCKET).upload(
        chemin, contenu, file_options={"content-type": "application/zip", "upsert": "true"},
    )
    marquer(import_id, storage_path=chemin, statut="analyse", source=source)
    taches.add_task(_analyser, import_id, contenu, source, str(organisation_id))
    return {"import_id": import_id, "source": source}


@router.get("/imports/{import_id}")
def lire(import_id: UUID):
    claims = get_claims()
    with _claims_conn(claims) as conn:
        row = une(
            conn,
            """
            select id::text, organisation_id::text, statut, source, rapport, erreur,
                   projet_cree_id::text, created_at, termine_le, parametres
              from bancarisation.import_projet where id = %s
            """,
            (str(import_id),),
        )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import introuvable.")
    return row


@router.post("/imports/{import_id}/lancer", status_code=status.HTTP_202_ACCEPTED)
def lancer(import_id: UUID, taches: BackgroundTasks):
    claims = get_claims()
    with _claims_conn(claims) as conn:
        row = une(
            conn,
            "select id::text, statut, source, storage_path, organisation_id::text, rapport, parametres from bancarisation.import_projet where id = %s",
            (str(import_id),),
        )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import introuvable.")
    if row["statut"] != "pret":
        raise HTTPException(status.HTTP_409_CONFLICT, "L'analyse n'est pas terminée, ou le dossier n'est pas importable.")
    erreurs = (row.get("rapport") or {}).get("erreurs") or []
    if erreurs:
        raise HTTPException(status.HTTP_409_CONFLICT, "Des erreurs bloquent la création du projet.")
    marquer(str(import_id), statut="en_cours")
    taches.add_task(_importer, str(import_id), row["storage_path"], row["organisation_id"], claims, row.get("source") or "archive", row.get("parametres") or {})
    return {"import_id": str(import_id)}


@router.post("/imports/{import_id}/analyse", status_code=status.HTTP_202_ACCEPTED)
def relancer(import_id: UUID, body: ParametresAnalyse, taches: BackgroundTasks):
    claims = get_claims()
    with _claims_conn(claims) as conn:
        row = une(
            conn,
            "select id::text, source, storage_path, organisation_id::text from bancarisation.import_projet where id = %s",
            (str(import_id),),
        )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import introuvable.")
    if row.get("source") != "modele":
        raise HTTPException(status.HTTP_409_CONFLICT, "Seule une analyse de modèle se relance.")
    parametres = {"sig": body.sig or {}, "figer_baseline": body.figer_baseline}
    marquer(str(import_id), statut="analyse", parametres=parametres)
    brut = get_supabase_admin().storage.from_(BUCKET).download(row["storage_path"])
    if isinstance(brut, memoryview):
        brut = brut.tobytes()
    taches.add_task(_analyser, str(import_id), brut, "modele", row["organisation_id"], parametres)
    return {"import_id": str(import_id)}


@router.post("/imports/{import_id}/confirmer", status_code=status.HTTP_202_ACCEPTED)
def confirmer(import_id: UUID, taches: BackgroundTasks):
    return lancer(import_id, taches)


def _analyser(import_id: str, contenu: bytes, source: str = "archive", organisation_id: str = "", parametres: dict | None = None) -> None:
    try:
        if source == "modele":
            from api.reimport.modele.pipeline import analyser_dossier
            with tempfile.TemporaryDirectory() as tmp:
                extraire_zip(contenu, Path(tmp))
                rapport = analyser_dossier(Path(tmp), organisation_id, import_id, parametres)
            rapport.pop("jeu", None)
            _clore_analyse(import_id, rapport, None)
            return
        with tempfile.TemporaryDirectory() as tmp:
            archive = ouvrir_zip(contenu, Path(tmp))
            colonnes = _colonnes_base()
            rapport = analyser_archive(archive, colonnes)
            manifest = {}
            if archive.existe("manifest.json"):
                import json
                manifest = json.loads(archive.lire("manifest.json").decode("utf-8"))
        _clore_analyse(import_id, rapport, manifest or None)
    except Exception as exc:
        logger.exception("Analyse import %s", import_id)
        marquer(import_id, statut="echec", erreur=str(exc)[:800])
        _oublier(import_id)


def _importer(import_id: str, storage_path: str, organisation_id: str, claims: dict, source: str = "archive", parametres: dict | None = None) -> None:
    try:
        brut = get_supabase_admin().storage.from_(BUCKET).download(storage_path)
        if isinstance(brut, memoryview):
            brut = brut.tobytes()
        with tempfile.TemporaryDirectory() as tmp:
            if source == "modele":
                from api.reimport.modele.pipeline import analyser_dossier
                racine = extraire_zip(brut, Path(tmp))
                rapport = analyser_dossier(racine, organisation_id, import_id, parametres)
                if not rapport.get("pret") or rapport.get("jeu") is None:
                    raise RuntimeError(" ; ".join(rapport.get("erreurs") or ["Analyse refusée."]))
                projet_id, avertissements = importer(None, organisation_id, claims, import_id, jeu=rapport["jeu"])
            else:
                archive = ouvrir_zip(brut, Path(tmp))
                projet_id, avertissements = importer(archive, organisation_id, claims, import_id)
        marquer(
            import_id,
            statut="termine",
            projet_cree_id=projet_id,
            rapport={"avertissements": avertissements, "projet_id": projet_id},
            termine_le=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        _oublier(import_id)
    except Exception as exc:
        logger.exception("Import %s", import_id)
        marquer(import_id, statut="echec", erreur=str(exc)[:800], termine_le=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        _oublier(import_id)


def _clore_analyse(import_id: str, rapport: dict, manifest) -> None:
    marquer(
        import_id,
        statut="pret" if rapport.get("pret") else "echec",
        rapport=rapport,
        manifest_source=manifest,
        erreur=None if rapport.get("pret") else " ; ".join(rapport.get("erreurs") or [])[:800],
    )
    if not rapport.get("pret"):
        _oublier(import_id)


def _oublier(import_id: str) -> None:
    from api.reimport.purge import oublier_zip
    oublier_zip(import_id, _chemin_stocke(import_id))


def _chemin_stocke(import_id: str) -> str | None:
    with connect_backend() as conn:
        row = une(conn, "select storage_path from bancarisation.import_projet where id = %s", (import_id,))
    return (row or {}).get("storage_path")


def _preparer(contenu: bytes, nom: str) -> tuple[str, bytes]:
    import io
    import zipfile
    if nom.lower().endswith(".xlsm"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Les fichiers .xlsm ne sont pas acceptés.")
    if nom.lower().endswith(".xlsx"):
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w") as archive:
            archive.writestr("projet.xlsx", contenu)
        return "modele", tampon.getvalue()
    with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
        noms = [info.filename for info in archive.infolist()]
    if any(nom.endswith("manifest.json") for nom in noms):
        return "archive", contenu
    if any(nom.endswith("projet.xlsx") for nom in noms):
        return "modele", contenu
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ni une archive KerERC, ni un modèle projet.xlsx.")


def _roles_referentiel() -> list[tuple[str, str]]:
    try:
        with connect_backend() as conn:
            rows = conn.execute(
                "select code, libelle from bancarisation.role_contact_ref order by ordre"
            ).fetchall()
        if rows:
            return [(r["code"], r["libelle"]) for r in rows]
    except Exception:
        logger.info("Rôles de contact lus depuis la liste embarquée")
    return ROLES


def _colonnes_base() -> dict[str, set[str]] | None:
    try:
        with connect_backend() as conn:
            rows = conn.execute(
                """
                select table_name, column_name from information_schema.columns
                 where table_schema = 'bancarisation'
                """
            ).fetchall()
    except Exception:
        logger.exception("Colonnes de la base illisibles pour l'analyse")
        return None
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(row["table_name"], set()).add(row["column_name"])
    return out
