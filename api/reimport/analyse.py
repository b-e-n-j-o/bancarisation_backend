"""Lecture d'un ZIP d'export et contrôles sans écriture en base."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

from api.reimport import FORMAT, NON_REPRIS, VERSIONS


class ArchiveExport:
    def __init__(self, racine: Path):
        self.racine = racine

    def lire(self, chemin: str) -> bytes:
        return (self.racine / chemin).read_bytes()

    def existe(self, chemin: str) -> bool:
        return (self.racine / chemin).is_file()


def extraire_zip(contenu: bytes, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
        total = 0
        if len(archive.infolist()) > 4000:
            raise RuntimeError("Archive trop volumineuse (trop de fichiers).")
        for info in archive.infolist():
            nom = info.filename
            if nom.startswith("/") or ".." in Path(nom).parts:
                raise RuntimeError(f"Chemin refusé dans l'archive : {nom}")
            total += info.file_size
            if total > 2 * 1024 * 1024 * 1024:
                raise RuntimeError("Archive trop volumineuse une fois décompressée.")
        archive.extractall(destination)
    return destination


def ouvrir_zip(contenu: bytes, destination: Path) -> ArchiveExport:
    extraire_zip(contenu, destination)
    manifests = list(destination.rglob("manifest.json"))
    if not manifests:
        raise RuntimeError("manifest.json absent.")
    return ArchiveExport(manifests[0].parent)


def analyser_archive(archive: ArchiveExport, colonnes_base: dict[str, set[str]] | None = None) -> dict:
    if not archive.existe("manifest.json"):
        return _bloque(["manifest.json absent."])
    manifest = json.loads(archive.lire("manifest.json").decode("utf-8"))
    erreurs: list[str] = []
    avertissements: list[str] = []
    if manifest.get("format") != FORMAT:
        erreurs.append(f"Format inconnu : {manifest.get('format')!r}.")
    version = str(manifest.get("version_format") or "")
    if version not in VERSIONS:
        erreurs.append(f"Version de format {version or 'absente'} non prise en charge (attendu : 1.1).")
    empreintes = []
    for entree in manifest.get("fichiers") or []:
        chemin = entree.get("chemin") or ""
        if not archive.existe(chemin):
            erreurs.append(f"Fichier manquant : {chemin}")
            continue
        sha = hashlib.sha256(archive.lire(chemin)).hexdigest()
        if sha != entree.get("sha256"):
            erreurs.append(f"Empreinte différente : {chemin}")
        else:
            empreintes.append(chemin)
    comptages: dict[str, int] = {}
    data = archive.racine / "data"
    if data.is_dir():
        for csv_path in sorted(data.glob("*.csv")):
            if csv_path.name.startswith("_"):
                continue
            with csv_path.open(encoding="utf-8", newline="") as fichier:
                lecteur = csv.reader(fichier)
                entete = next(lecteur, [])
                n = sum(1 for _ in lecteur)
            comptages[csv_path.stem] = n
            if colonnes_base and csv_path.stem in colonnes_base:
                inconnues = [c for c in entete if c.removesuffix("_wkt") not in colonnes_base[csv_path.stem] and not c.endswith("_wkt")]
                # columns ending _wkt map to geometry; unknown others warn
                for col in entete:
                    cible = "geom" if col.endswith("_wkt") else col
                    if cible not in colonnes_base[csv_path.stem] and not col.endswith("_wkt"):
                        avertissements.append(f"{csv_path.name} : colonne {col} absente de la base, ignorée.")
                _ = inconnues
    if not archive.existe("data/_fichiers.csv"):
        erreurs.append("data/_fichiers.csv absent : impossible de relier les documents.")
    else:
        with (archive.racine / "data" / "_fichiers.csv").open(encoding="utf-8", newline="") as fichier:
            for ligne in csv.DictReader(fichier):
                chemin = ligne.get("chemin_archive") or ""
                if not archive.existe(chemin):
                    erreurs.append(f"Document introuvable dans le ZIP : {chemin}")
                    continue
                sha = hashlib.sha256(archive.lire(chemin)).hexdigest()
                if ligne.get("sha256") and sha != ligne["sha256"]:
                    erreurs.append(f"Empreinte du document différente : {chemin}")
    acces = _acces_origine(archive)
    rapport = {
        "pret": not erreurs,
        "erreurs": erreurs,
        "avertissements": avertissements,
        "comptages": comptages,
        "nb_fichiers_verifies": len(empreintes),
        "non_repris": list(NON_REPRIS),
        "acces_origine": acces,
        "projet_source": (manifest.get("projet") or {}),
        "organisation_source": (manifest.get("organisation") or {}),
    }
    return rapport


def _bloque(erreurs: list[str]) -> dict:
    return {"pret": False, "erreurs": erreurs, "avertissements": [], "comptages": {}, "non_repris": list(NON_REPRIS)}


def _acces_origine(archive: ArchiveExport) -> list[dict]:
    chemin = archive.racine / "data" / "projet_acces.csv"
    if not chemin.is_file():
        return []
    with chemin.open(encoding="utf-8", newline="") as fichier:
        return list(csv.DictReader(fichier))
