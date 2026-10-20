"""Test du module 2 sans réseau : les appels API sont remplacés par des réponses simulées.

Vérifie : sélection des zones, recadrage, cache (second passage sans appel), gestion d'une
erreur sur une zone, et réinjection par le module 3.

    python -m pdfvisuel.tests.test_m2_simulation chemin/vers/plan.pdf
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from ..m1_detection.profil import analyser_pdf, ecrire_sorties
from ..m2_vision.vision import executer
from ..m3_assemblage.assemblage import assembler


def faux_ocr(png, modele, cle):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return {"markdown": "| Bosquet | Plants |\n|---|---|\n| 3 plants | 1 baliveau + 2 jeunes plants |",
            "incertitudes": []}, {"pages_processed": 1}


def faux_ocr_en_erreur(png, modele, cle):
    raise RuntimeError("HTTP 503 simulé")


def faux_vision(png, prompt, modele, cle):
    return {"type_axe": "mois", "colonnes": ["Janvier"], "lignes": [], "cellules_entete_colorees": [],
            "incertitudes": []}, {"prompt_tokens": 1000, "completion_tokens": 50}


def main(pdf: str) -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        profil = analyser_pdf(pdf)
        ecrire_sorties(profil, tmp, overlays="aucun")
        attendues = [z for z in profil["routage"] if z["action"] in ("vision_zone", "ocr_page")]

        s1 = executer(tmp / "profil.json", False, False, "normale", "modele-v", "modele-ocr",
                      appel_vision=faux_vision, appel_ocr=faux_ocr)
        assert len(s1["zones"]) == len(attendues), (len(s1["zones"]), len(attendues))
        assert s1["cout"]["appels"] == len(attendues)
        assert all((tmp / "vision" / z["image"]).exists() for z in s1["zones"])

        s2 = executer(tmp / "profil.json", False, False, "normale", "modele-v", "modele-ocr",
                      appel_vision=faux_vision, appel_ocr=faux_ocr_en_erreur)
        assert s2["cout"]["appels"] == 0 and s2["cout"]["depuis_cache"] == len(attendues), s2["cout"]

        shutil.rmtree(tmp / "vision" / "cache")
        s3 = executer(tmp / "profil.json", False, False, "normale", "modele-v", "modele-ocr",
                      appel_vision=faux_vision, appel_ocr=faux_ocr_en_erreur)
        assert all(z["statut"] == "erreur" for z in s3["zones"]), "une erreur API doit être isolée par zone"

        # Audit : les grilles vectorielles partent aussi en vision avec --verifier-grilles
        s4 = executer(tmp / "profil.json", False, True, "normale", "modele-v", "modele-ocr",
                      appel_vision=faux_vision, appel_ocr=faux_ocr)
        n_grilles = sum(len(p["grilles"]) for p in profil["pages"])
        assert len(s4["zones"]) == len(attendues) + n_grilles

        r = assembler(tmp / "profil.json", tmp / "vision" / "vision.json", None)
        texte = (tmp / "document_enrichi.md").read_text()
        assert "source=vision" in texte and "1 baliveau + 2 jeunes plants" in texte
        controles = json.loads((tmp / "controles.json").read_text())
        assert not [c for c in controles if c["controle"] == "zone_non_lue"], "zones lues => plus d'alerte"
        print(f"OK — {len(attendues)} zones simulées, cache vérifié, erreurs isolées, "
              f"{n_grilles} grilles en audit, assemblage : {r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1])
