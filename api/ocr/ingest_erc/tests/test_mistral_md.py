"""Non-régression sur des extraits réels de la sortie Mistral OCR (plan de gestion Le Barp).
python -m tests.test_mistral_md   (depuis backend/api/ocr/ingest_erc)
"""
import re
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest_erc.extract_referentiel import faits_arrete, faits_plan, faits_tableaux_md
from ingest_erc.inventaire import annexes_embarquees, role_pdf
from ingest_erc.mdnorm import fiabilite_nombres, markdown_vers_texte, retirer_entetes
from ingest_erc.modeles import Document
from ingest_erc.ocr import pages_depuis_markdown

FX = Path(__file__).parent / "fixtures_mistral"
PIED = ("ECO-COMPENSATION – SIMEHTIS – FAGE – Décembre 2021\n\nOpération de lycée / collège sur la commune "
        "du Barp (33) – Plan de gestion des espaces de compensation et des zones évitées\n\nRégion Nouvelle-Aquitaine\n\n")


def document():
    nums = {int(re.search(r"p(\d+)", f.name).group(1)): f.read_text() for f in FX.glob("p*.md")}
    pages = pages_depuis_markdown([nums.get(i, PIED + str(i)) for i in range(1, 103)])
    for p, t in zip(pages, retirer_entetes([p.texte for p in pages])):
        p.texte = t
    return Document(nom="PG.pdf", chemin="", sha256="x", extension=".pdf", role="plan_gestion", pages=pages)


def test_tout():
    d = document()
    assert role_pdf(d.pages)[0] == "plan_gestion"                      # le sommaire cite des arrêtés
    assert "# TU 1 : Adapter" in d.pages[39].texte                     # titres #### normalisés
    assert "Unités de gestion : UG 1" in d.pages[39].texte             # gras retiré
    assert "![" not in d.pages[0].texte
    an = {a["num"]: a for a in annexes_embarquees(d)}
    assert an[1]["page_debut"] == 82 and an[2]["page_debut"] == 89 and an[3]["page_debut"] == 100

    with patch("ingest_erc.extract_referentiel.llm.actif", return_value=False), \
         patch("ingest_erc.pdf_sections.llm.actif", return_value=False):
        f_plan, sel = faits_plan(d)
    assert set(sel["familles"]) == {"TU", "TE", "SE", "MG"}
    assert {f.cle for f in f_plan if f.type == "famille_code"} == {"TU", "TE", "SE", "MG"}
    assert [f.valeur for f in f_plan if f.type == "action_ug" and f.cle == "TU1"] == [["UG1"]]

    f_md = faits_tableaux_md(d, sel["familles"])
    ug = {f.cle: f for f in f_md if f.type == "action_ug"}
    assert len(ug) == 13
    assert ug["SE2"].confiance_extraction < 1 and ug["SE1"].confiance_extraction == 1   # cellule fusionnée
    intit = {f.cle: f.valeur for f in f_md if f.type == "action_intitule"}
    assert intit["TU1"].startswith("Adapter")                          # pas l'objectif opérationnel
    mes = {f.cle: f.valeur for f in f_md if f.type == "mesure_plan"}
    assert mes["EV-3"].get("non_soumis") and mes["C2"]["surface_ha"] == 14.88
    assert abs(mes["EV-2"]["surface_ha"] - 0.3) < 1e-9
    parc = [f.valeur["parcelle"] for f in f_md if f.type == "parcelle_tableau"]
    assert parc == ["C480c", "C444b", "C480b", "C480a", "D2a"]          # suite sans en-tête (p.27)
    assert any(f.type == "ancre_temporelle" and f.valeur["etat_zero"] == 2022 for f in f_md)
    assert not any(f.source.loc.startswith("p.55") for f in f_md if f.type != "ancre_temporelle")

    sous = [p for p in d.pages if 82 <= p.num <= 88]
    a1 = {f.type: f.valeur for f in faits_arrete(d, pages=sous, cle="annexe1")}
    assert a1["decision_reference"] == "n° 21-037" and a1["decision_date"] == "2021-09-13"
    assert a1["obligation_boisement"]["surface_ha"] == 15.897 and a1["indemnite"] == 58821

    assert fiabilite_nombres("| 1 400,00 € | 800,00 € | 9 900,00 € |", "1 800,00 €    900,00 €    9 950,00 €    700,00 €    700,00 €    3 000,00 €") == 0.0
    assert markdown_vers_texte("|  **I. CONTEXTE** | **5**  |\n| --- | --- |") == "I. CONTEXTE    5"
    print("OK")


if __name__ == "__main__":
    test_tout()
