"""ZIP de l'export et dépôt dans le bucket privé exports-projet."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import zipfile
from pathlib import Path

import httpx

from api.db.supabase import get_supabase_admin
from api.export.contexte import FichierExport

logger = logging.getLogger("api.export.archive")

BUCKET = "exports-projet"
SEUIL_TUS = 6 * 1024 * 1024
_DEJA_COMPRESSES = {".pdf", ".png", ".jpg", ".jpeg", ".zip", ".gpkg"}


def ecrire_zip(dossier: Path, racine: str, fichiers: list[FichierExport], manifest: Path) -> Path:
    cible = dossier / "export.zip"
    with zipfile.ZipFile(cible, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for fic in fichiers:
            mode = _mode(fic)
            arcname = f"{racine}/{fic.chemin_archive}"
            _ecrire_entree(archive, fic.chemin_local, arcname, mode)
        _ecrire_entree(archive, manifest, f"{racine}/manifest.json", zipfile.ZIP_DEFLATED)
    return cible


def _mode(fic: FichierExport) -> int:
    if not fic.compresser or fic.chemin_local.suffix.lower() in _DEJA_COMPRESSES:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def _ecrire_entree(archive: zipfile.ZipFile, source: Path, arcname: str, mode: int) -> None:
    info = zipfile.ZipInfo(filename=arcname)
    info.compress_type = mode
    info.flag_bits |= 0x800
    with source.open("rb") as brut, archive.open(info, "w") as dest:
        while True:
            bloc = brut.read(1024 * 1024)
            if not bloc:
                break
            dest.write(bloc)


def empreinte_fichier(path: Path) -> tuple[str, int]:
    hachage = hashlib.sha256()
    taille = 0
    with path.open("rb") as fichier:
        for bloc in iter(lambda: fichier.read(1024 * 1024), b""):
            hachage.update(bloc)
            taille += len(bloc)
    return hachage.hexdigest(), taille


def uploader(path: Path, storage_path: str) -> None:
    taille = path.stat().st_size
    if taille >= SEUIL_TUS:
        _uploader_tus(path, storage_path, taille)
        return
    client = get_supabase_admin()
    with path.open("rb") as fichier:
        client.storage.from_(BUCKET).upload(
            storage_path,
            fichier.read(),
            file_options={"content-type": "application/zip", "upsert": "true"},
        )


def _uploader_tus(path: Path, storage_path: str, taille: int) -> None:
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    cle = (
        os.environ.get("SERVICE_ROLE_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    if not base or not cle:
        raise RuntimeError("SUPABASE_URL et SERVICE_ROLE_KEY requis pour l'upload résumable.")
    meta = ",".join(
        [
            _meta("bucketName", BUCKET),
            _meta("objectName", storage_path),
            _meta("contentType", "application/zip"),
            _meta("cacheControl", "3600"),
        ]
    )
    entetes = {
        "Authorization": f"Bearer {cle}",
        "Tus-Resumable": "1.0.0",
        "Upload-Length": str(taille),
        "Upload-Metadata": meta,
        "x-upsert": "true",
    }
    with httpx.Client(timeout=120) as client:
        creation = client.post(f"{base}/storage/v1/upload/resumable", headers=entetes)
        if creation.status_code not in (201, 204):
            raise RuntimeError(
                f"Création TUS refusée ({creation.status_code}) : {creation.text[:300]}"
            )
        location = creation.headers.get("Location") or creation.headers.get("location")
        if not location:
            raise RuntimeError("Réponse TUS sans en-tête Location.")
        if location.startswith("/"):
            location = f"{base}{location}"
        offset = 0
        with path.open("rb") as fichier:
            while offset < taille:
                bloc = fichier.read(SEUIL_TUS)
                if not bloc:
                    break
                patch = client.patch(
                    location,
                    content=bloc,
                    headers={
                        "Authorization": f"Bearer {cle}",
                        "Tus-Resumable": "1.0.0",
                        "Upload-Offset": str(offset),
                        "Content-Type": "application/offset+octet-stream",
                    },
                )
                if patch.status_code not in (204, 200):
                    raise RuntimeError(
                        f"PATCH TUS refusé ({patch.status_code}) à l'offset {offset}."
                    )
                offset = int(patch.headers.get("Upload-Offset", offset + len(bloc)))
    logger.info("ZIP déposé via TUS : %s (%s octets)", storage_path, taille)


def _meta(cle: str, valeur: str) -> str:
    return f"{cle} {base64.b64encode(valeur.encode()).decode()}"


def url_signee(storage_path: str, nom_telechargement: str, expires_in: int = 900) -> str:
    client = get_supabase_admin()
    result = client.storage.from_(BUCKET).create_signed_url(
        path=storage_path,
        expires_in=expires_in,
        options={"download": nom_telechargement},
    )
    url = _extraire_url(result)
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not base:
        return url
    return f"{base}{url}" if url.startswith("/") else f"{base}/{url}"


def _extraire_url(result: object) -> str:
    if isinstance(result, dict):
        for cle in ("signedURL", "signedUrl", "signed_url"):
            if result.get(cle):
                return str(result[cle])
        data = result.get("data")
        if isinstance(data, dict):
            for cle in ("signedURL", "signedUrl"):
                if data.get(cle):
                    return str(data[cle])
    for attr in ("signedURL", "signedUrl", "signed_url"):
        valeur = getattr(result, attr, None)
        if valeur:
            return str(valeur)
    raise RuntimeError("URL signée introuvable dans la réponse storage.")


def supprimer_objet(storage_path: str) -> None:
    client = get_supabase_admin()
    client.storage.from_(BUCKET).remove([storage_path])
