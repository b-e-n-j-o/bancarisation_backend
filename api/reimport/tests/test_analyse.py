"""Analyse d'une archive KerERC sans base."""

import hashlib
import json
from pathlib import Path

from api.reimport.analyse import ArchiveExport, analyser_archive


def test_version_inconnue_bloquee(tmp_path: Path):
    racine = tmp_path / "KerERC_demo"
    racine.mkdir()
    (racine / "manifest.json").write_text(json.dumps({
        "format": "kererc-export-projet",
        "version_format": "1.0",
        "fichiers": [],
    }), encoding="utf-8")
    rapport = analyser_archive(ArchiveExport(racine))
    assert rapport["pret"] is False
    assert any("1.1" in message for message in rapport["erreurs"])


def test_empreinte_et_index_documents(tmp_path: Path):
    racine = tmp_path / "KerERC_demo"
    (racine / "data").mkdir(parents=True)
    (racine / "05_Documents").mkdir()
    contenu = b"arrete"
    sha = hashlib.sha256(contenu).hexdigest()
    (racine / "05_Documents" / "arrete.pdf").write_bytes(contenu)
    (racine / "manifest.json").write_text(json.dumps({
        "format": "kererc-export-projet",
        "version_format": "1.1",
        "fichiers": [{
            "chemin": "05_Documents/arrete.pdf",
            "sha256": sha,
            "module": "documents",
            "description": "Arrêté",
        }],
        "projet": {"nom": "Étang"},
    }), encoding="utf-8")
    with (racine / "data" / "_fichiers.csv").open("w", encoding="utf-8") as fichier:
        fichier.write("document_id,chemin_archive,sha256\n")
        fichier.write(f"doc-1,05_Documents/arrete.pdf,{sha}\n")
    with (racine / "data" / "projets.csv").open("w", encoding="utf-8") as fichier:
        fichier.write("id,nom\nabc,Etang\n")
    rapport = analyser_archive(ArchiveExport(racine))
    assert rapport["pret"] is True
    assert rapport["comptages"]["projets"] == 1
    assert any("partenaires" in message for message in rapport["non_repris"])
