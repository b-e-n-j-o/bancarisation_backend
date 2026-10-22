"""Assemble l'analyse d'un dossier modèle, sans rien écrire en base."""

from __future__ import annotations

from pathlib import Path

from api.reimport.modele.constats import Constat
from api.reimport.modele.lecteur_documents import lire_documents
from api.reimport.modele.lecteur_excel import lire_classeur
from api.reimport.modele.lecteur_sig import lire_sig
from api.reimport.modele.normaliser import code_ug
from api.reimport.modele.traduction import construire


def analyser_dossier(racine: Path, organisation_id: str, import_id: str, parametres: dict | None) -> dict:
    parametres = parametres or {}
    classeur = _trouver_classeur(racine)
    if classeur is None:
        return _rapport([Constat("erreur", None, None, None, "projet.xlsx est introuvable.")], {}, None)
    donnees, constats = lire_classeur(classeur)
    if any(c.niveau == "erreur" for c in constats):
        return _rapport(constats, {}, None)
    codes = {code_ug(l.get("Code")) for l in donnees.get("ug") or [] if code_ug(l.get("Code"))}
    commune = texte_commune(donnees)
    geometries, sig, apercu = lire_sig(racine, codes, (parametres.get("sig") or None), commune)
    constats.extend(sig)
    choix = any("Choisissez le champ" in c.message for c in sig)
    if choix or any(c.niveau == "erreur" for c in constats):
        rapport = _rapport(constats, {"sig": apercu}, None)
        rapport["choix_sig"] = choix
        return rapport
    figer = parametres.get("figer_baseline", True)
    jeu, suite, resume = construire(donnees, organisation_id, import_id, geometries, bool(figer))
    constats.extend(suite)
    lignes, fichiers, docs, resume_docs = lire_documents(racine, jeu.tables["projets"][0]["id"])
    constats.extend(docs)
    if lignes:
        jeu.tables["documents"] = lignes
        jeu.fichiers = fichiers
    resume["documents"] = resume_docs
    resume["sig"] = apercu
    erreurs = [c for c in constats if c.niveau == "erreur"]
    return {
        "pret": not erreurs,
        "choix_sig": False,
        "constats": [c.pour_json() for c in constats],
        "erreurs": [c.message for c in erreurs],
        "avertissements": [c.message for c in constats if c.niveau == "avertissement"],
        "resume": resume,
        "projet_source": {"nom": (donnees.get("projet") or {}).get("Nom")},
        "jeu": jeu,
    }


def texte_commune(donnees: dict) -> str:
    projet = donnees.get("projet") or {}
    return str(projet.get("Commune") or "").strip()


def _trouver_classeur(racine: Path) -> Path | None:
    direct = racine / "projet.xlsx"
    if direct.is_file():
        return direct
    trouves = [p for p in racine.rglob("projet.xlsx") if "_meta" or True]
    return trouves[0] if trouves else None


def _rapport(constats, resume, jeu) -> dict:
    return {
        "pret": False,
        "constats": [c.pour_json() for c in constats],
        "erreurs": [c.message for c in constats if c.niveau == "erreur"],
        "avertissements": [],
        "resume": resume,
        "jeu": jeu,
    }
