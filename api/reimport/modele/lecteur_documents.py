"""Documents classés par le nom du sous-dossier."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from api.reimport.jeu_donnees import FichierImport
from api.reimport.modele.constats import Constat
from api.reimport.modele.listes import ALIAS_CATEGORIES, EXTENSIONS

_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_MAX = 2 * 1024 * 1024 * 1024


def lire_documents(racine: Path, projet_id: str) -> tuple[list[dict], dict[str, dict], list[Constat], dict]:
    import uuid
    dossier = racine / "documents"
    if not dossier.is_dir():
        return [], {}, [], {"nb": 0, "octets": 0, "par_categorie": {}}
    constats: list[Constat] = []
    lignes = []
    fichiers = {}
    par_categorie: dict[str, int] = {}
    total = 0
    for chemin in sorted(p for p in dossier.rglob("*") if p.is_file()):
        if chemin.suffix.lower() not in EXTENSIONS:
            constats.append(Constat("avertissement", "Documents", None, chemin.name, "Extension non acceptée, fichier ignoré."))
            continue
        taille = chemin.stat().st_size
        if taille > _MAX:
            constats.append(Constat("erreur", "Documents", None, chemin.name, "Fichier plus grand que la limite de stockage."))
            continue
        total += taille
        relatif = chemin.relative_to(dossier)
        categorie = _categorie(relatif.parts[0] if len(relatif.parts) > 1 else "")
        if len(relatif.parts) == 1 or categorie == "autre" and relatif.parts[0].lower() not in ALIAS_CATEGORIES:
            if len(relatif.parts) == 1:
                constats.append(Constat("avertissement", "Documents", None, chemin.name, "Fichier à la racine de documents/ : catégorie « autre »."))
                categorie = "autre"
        date_doc = _DATE.match(chemin.name)
        doc_id = str(uuid.uuid4())
        sha = hashlib.sha256(chemin.read_bytes()).hexdigest()
        lignes.append({
            "id": doc_id,
            "projet_id": projet_id,
            "nom": chemin.stem,
            "nom_fichier": chemin.name,
            "categorie": categorie,
            "taille_octets": taille,
            "date_document": date_doc.group(1) if date_doc else None,
            "bucket_path": "",
        })
        fichiers[doc_id] = {"chemin_local": str(chemin), "nom": chemin.name, "sha256": sha, "categorie": categorie}
        par_categorie[categorie] = par_categorie.get(categorie, 0) + 1
    return lignes, fichiers, constats, {"nb": len(lignes), "octets": total, "par_categorie": par_categorie}


def _categorie(nom: str) -> str:
    if not nom:
        return "autre"
    pli = unicodedata.normalize("NFD", nom.strip().lower())
    pli = "".join(c for c in pli if unicodedata.category(c) != "Mn")
    pli = pli.replace(" ", "_").replace("-", "_")
    return ALIAS_CATEGORIES.get(pli, "autre")
