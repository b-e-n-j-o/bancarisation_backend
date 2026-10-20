"""Test de la fusion des frises coupées, du rattachement et de l'insertion dans le texte OCR.

Cas c14 du corpus : titre « SE1 : … » au-dessus d'une frise de 30 ans dessinée sur deux
rangées, sans libellé de ligne. On simule la sortie OCR Mistral de cette page (deux tableaux
Markdown sans les couleurs) et on vérifie les trois modes d'insertion.

    python -m pdfvisuel.tests.test_m3_insertion
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from ..evaluation.generer_corpus import c14
from ..evaluation.mini_pdf import Document
from ..m1_detection.profil import analyser_pdf, ecrire_sorties
from ..m3_assemblage.assemblage import assembler

OCR_PAGE = """# SE1 : Suivi piézométrique des sondes

Les relevés des sondes sont réalisés deux fois par an sur les cinq premières années, puis tous les cinq ans.

| 2023 | 2024 | 2025 | 2026 | 2027 | 2028 | 2029 | 2030 | 2031 | 2032 | 2033 | 2034 | 2035 | 2036 | 2037 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |

| 2038 | 2039 | 2040 | 2041 | 2042 | 2043 | 2044 | 2045 | 2046 | 2047 | 2048 | 2049 | 2050 | 2051 | 2052 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |

Le rapport de suivi est transmis chaque année de relevé à la DDTM.

| Poste | Coût HT |
|---|---|
| Sondes piézométriques | 3 600 € |
"""


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        doc = Document()
        c14(doc)
        pdf = tmp / "c14.pdf"
        doc.save(pdf)

        # Module 1 : une seule grille de 30 colonnes, rattachée à SE1
        profil = analyser_pdf(str(pdf))
        grilles = profil["pages"][0]["grilles"]
        assert len(grilles) == 1, f"frise non fusionnée : {len(grilles)} grilles"
        g = grilles[0]
        assert len(g["colonnes"]) == 30 and len(g["fusion_de"]) == 2, g.get("fusion_de")
        assert g["contexte"]["code_proche"] == "SE1", g["contexte"]
        ecrire_sorties(profil, tmp, overlays="aucun")
        (tmp / "ocr.json").write_text(json.dumps({"pages": [{"index": 0, "markdown": OCR_PAGE}]}, ensure_ascii=False))

        # Mode remplacer : les deux tableaux OCR disparaissent, la grille prend leur place
        assembler(tmp / "profil.json", None, tmp / "ocr.json", "remplacer")
        texte = (tmp / "document_enrichi.md").read_text()
        trace = json.loads((tmp / "insertions.json").read_text())
        assert trace[0]["decision"] == "remplace" and len(trace[0]["tableaux_ocr"]) == 2, trace
        lignes_ocr = [l for l in texte.splitlines() if l.startswith("| 2023 |") or l.startswith("| 2038 |")]
        assert not lignes_ocr, "tableau OCR non remplacé"
        assert texte.index("cinq ans.") < texte.index("[GRILLE p001_g1") < texte.index("Le rapport de suivi")
        assert "fiche SE1 probable" in texte and "2052" in texte
        assert "| 2023 | 2024 |" in trace[0]["texte_ocr_remplace"], "le texte OCR remplacé doit être tracé"
        assert "| Sondes piézométriques | 3 600 € |" in texte, "un tableau sans rapport ne doit pas être touché"

        # Mode accompagner : tableaux OCR conservés, grille juste après eux
        assembler(tmp / "profil.json", None, tmp / "ocr.json", "accompagner")
        texte = (tmp / "document_enrichi.md").read_text()
        trace = json.loads((tmp / "insertions.json").read_text())
        assert trace[0]["decision"] == "accompagne", trace
        assert texte.index("\n| 2038 | 2039 |") < texte.index("[GRILLE p001_g1") < texte.index("Le rapport de suivi")

        # Sans OCR (couche texte native) : insertion après la ligne d'ancrage
        assembler(tmp / "profil.json", None, None)
        texte = (tmp / "document_enrichi.md").read_text()
        trace = json.loads((tmp / "insertions.json").read_text())
        assert trace[0]["decision"] in ("insere", "insere_avant"), trace
        assert texte.index("[GRILLE p001_g1") < texte.index("Le rapport de suivi")
        print("OK — frise fusionnée (2 rangées → 30 colonnes), rattachée à SE1, "
              "tableaux OCR remplacés / accompagnés / insertion sur texte natif")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
