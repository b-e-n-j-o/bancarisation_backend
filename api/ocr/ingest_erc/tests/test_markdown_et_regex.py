"""Regex de la passe 1 : mêmes motifs sur pdftotext et sur markdown Mistral normalisé."""
from __future__ import annotations

import unittest
from pathlib import Path

from api.ocr.ingest_erc.ingest_erc.corrections import appliquer_aux_faits, faits_depuis_corrections
from api.ocr.ingest_erc.ingest_erc.extract_referentiel import RE_FAMILLE, faits_arrete
from api.ocr.ingest_erc.ingest_erc.inventaire import annexes_embarquees
from api.ocr.ingest_erc.ingest_erc.modeles import Document, Fait, PageTexte, Source
from api.ocr.ingest_erc.ingest_erc.ocr import markdown_vers_texte, pages_texte_brut
from api.ocr.ingest_erc.ingest_erc.pdf_sections import decouper, familles_codes
from api.ocr.ingest_erc.ingest_erc.reconcile_ref import Ctx, _actions, premiere_ligne_utile

AUDIT = Path(__file__).resolve().parents[1] / "audit_ocr"

PDFTOTEXT_PLAN_P1 = """\
OPERATION DE LYCEE / COLLEGE SUR LA COMMUNE DE BARP (33)
PLAN DE GESTION DES ESPACES DE COMPENSATION ET DES ZONES EVITEES - 2022 - 2027
"""

PDFTOTEXT_FAMILLES = """\
   Les travaux uniques (TU) : Travaux de restauration (réouverture de milieu, plantations,…) ;
   Les travaux d'entretien (TE) : Opérations visant à entretenir les milieux suite aux travaux de restauration ;
   Les suivis et études (SE) : Amélioration des connaissances du site mis en gestion ;
   Mise en œuvre générale du plan de gestion (MG) : Missions associées à la coordination
"""

PDFTOTEXT_FICHE = """\
 TU 1 : Adapter les itinéraires techniques sylvicoles en faveur de la Fauvette pitchou et des oiseaux landicoles
 Unités de gestion : UG 1
 Parcelles concernées : 330290B1103, 330290B301, 330290C1064b, 330290C1082, 330290B287, 330290C1076, 330290C252, 330290C974c
"""

PDFTOTEXT_TDM = """\
VII. ANNEXES ................................................................................................................................ 82
       7.1.        ANNEXE N°1 – ARRETE PREFECTORAL DE DEMANDE D'AUTORISATION DE DEFRICHEMENT ............... 82
       7.2.        ANNEXE N°2 – ARRETE PREFECTORAL DE DEMANDE DE DEROGATION AUX INTERDICTIONS ............... 89
       7.3.        ANNEXE N°3 – DETAIL ESTIMATIF DES COUTS .................................................................................... 100
"""

PDFTOTEXT_ARRETE_P1 = """\
     ARRÊTÉ portant dérogation aux interdictions de destruction de spécimens d'espèces
                         animales protégées et de leurs habitats
       Construction d'un nouveau lycée et nouveau collège, sur la commune du Barp (33)
                                         Région Nouvelle-Aquitaine
Réf. DBEC : n° 90/2021
                                          La Préfète de la Gironde
"""


def _doc(nom: str, role: str, pages: list[PageTexte]) -> Document:
    return Document(nom=nom, chemin="", sha256="", extension=".pdf", role=role, pages=pages)


def _pages_md(*relatifs: str) -> list[PageTexte]:
    out = []
    for i, rel in enumerate(relatifs, 1):
        p = AUDIT / rel
        out.append(PageTexte(num=i, texte=p.read_text(encoding="utf-8"), nb_mots=10))
    return pages_texte_brut(out)


class TestMarkdownVersTexte(unittest.TestCase):
    def test_images_titres_puces_tableaux(self):
        md = (
            "![Logo of X]()\n\n"
            "## PLAN DE GESTION DES ESPACES\n\n"
            "- **Les travaux uniques (TU)** : Travaux ;\n\n"
            "| TU 1 | UG 1 |\n"
            "| --- | --- |\n"
            "| TE 1 | UG 1 et UG 3 |\n"
        )
        txt = markdown_vers_texte(md)
        self.assertNotIn("![", txt)
        self.assertNotIn("**", txt)
        self.assertIn("# PLAN DE GESTION", txt)
        self.assertNotIn("##", txt)
        self.assertIn("Les travaux uniques (TU) : Travaux", txt)
        self.assertIn("TU 1    UG 1", txt)
        self.assertNotIn("| --- |", txt)
        self.assertEqual(txt, markdown_vers_texte(txt))

    def test_premiere_ligne_plan_apres_logos(self):
        if not (AUDIT / "plan/p01-05_titre_tdm/page_01.md").exists():
            self.skipTest("audit OCR absent")
        pages = _pages_md("plan/p01-05_titre_tdm/page_01.md")
        self.assertEqual(
            premiere_ligne_utile(pages[0].texte)[:20],
            "OPERATION DE LYCEE /"[:20],
        )
        self.assertEqual(
            premiere_ligne_utile(PDFTOTEXT_PLAN_P1)[:20],
            "OPERATION DE LYCEE /"[:20],
        )


class TestRegexDeuxCorpus(unittest.TestCase):
    def test_familles_sur_markdown_et_pdftotext(self):
        md = markdown_vers_texte(
            "- **Les travaux uniques (TU)** : Travaux ;\n"
            "- **Les travaux d'entretien (TE)** : Entretien ;\n"
            "- **Les suivis et études (SE)** : Suivis ;\n"
            "- **Mise en œuvre générale du plan de gestion (MG)** : Pilotage\n"
        )
        for corpus in (md, PDFTOTEXT_FAMILLES):
            codes = {m.group(2) for m in RE_FAMILLE.finditer(corpus)}
            self.assertTrue({"TU", "TE", "SE", "MG"} <= codes, corpus)

    def test_fiche_et_parcelle_974c(self):
        if (AUDIT / "plan/p40-41_fiche_TU1/page_40.md").exists():
            md = pages_texte_brut([
                PageTexte(num=40, texte=(AUDIT / "plan/p40-41_fiche_TU1/page_40.md").read_text(), nb_mots=10)
            ])[0].texte
        else:
            md = markdown_vers_texte(
                "#### TU 1 : Adapter les itinéraires\n\n**Unités de gestion :** UG 1\n\n"
                "**Parcelles concernées :** 330290C974c\n"
            )
        for corpus in (md, PDFTOTEXT_FICHE):
            self.assertRegex(corpus, r"TU\s?1\s*:")
            self.assertRegex(corpus, r"(?i)unit[ée]s? de gestion\s*:")
            self.assertIn("330290C974c", corpus)

    def test_familles_codes_et_fiches_markdown(self):
        p38 = AUDIT / "plan/p37-39_familles_tabl5/page_38.md"
        p40 = AUDIT / "plan/p40-41_fiche_TU1/page_40.md"
        if not (p38.exists() and p40.exists()):
            self.skipTest("audit OCR absent")
        pages = pages_texte_brut([
            PageTexte(num=38, texte=p38.read_text(encoding="utf-8"), nb_mots=10),
            PageTexte(num=40, texte=p40.read_text(encoding="utf-8"), nb_mots=10),
        ])
        fam = familles_codes(pages)
        self.assertIn("TU", fam)
        self.assertGreaterEqual(len(fam["TU"]), 2)
        fiches = [s for s in decouper(pages, fam) if s.fiche]
        self.assertTrue(any("TU1" in s.titre.replace(" ", "") for s in fiches), [s.titre for s in fiches])

    def test_projet_libelle_arrete(self):
        for texte, label in (
            (PDFTOTEXT_ARRETE_P1, "pdftotext"),
            (
                markdown_vers_texte(
                    (AUDIT / "arrete/p01_libelle/page_01.md").read_text()
                    if (AUDIT / "arrete/p01_libelle/page_01.md").exists()
                    else "# **ARRÊTÉ portant dérogation aux interdictions de destruction de spécimens d'espèces "
                         "animales protégées et de leurs habitats**\n\n"
                         "**Construction d'un nouveau lycée et nouveau collège, sur la commune du Barp (33)**\n\n"
                         "Réf. DBEC : n° 90/2021\n\n**La Préfète de la Gironde**\n"
                ),
                "mistral",
            ),
        ):
            doc = _doc("arrete.pdf", "arrete", [PageTexte(num=1, texte=texte, nb_mots=40)])
            faits = faits_arrete(doc)
            libs = [f.valeur for f in faits if f.type == "projet_libelle"]
            self.assertTrue(libs, label)
            self.assertIn("Barp", libs[0], label)

    def test_annexes_tdm_points_et_tableau(self):
        pages_pt = [PageTexte(num=i, texte=PDFTOTEXT_TDM if i == 2 else "x", nb_mots=20) for i in range(1, 8)]
        pages_pt.append(PageTexte(num=82, texte="7.1. Annexe n°1 – Arrêté préfectoral de demande d'autorisation de défrichement", nb_mots=10))
        doc = _doc("plan.pdf", "plan_gestion", pages_pt)
        an = annexes_embarquees(doc)
        self.assertTrue(any(a["num"] == 1 and "defrichement" in a["titre"].lower().replace("è", "e").replace("ê", "e")
                            or (a["num"] == 1 and "defrich" in a["titre"].lower()) for a in an), an)

        if (AUDIT / "plan/p01-05_titre_tdm/page_02.md").exists():
            md2 = markdown_vers_texte((AUDIT / "plan/p01-05_titre_tdm/page_02.md").read_text())
            pages = [PageTexte(num=1, texte="plan", nb_mots=3)]
            pages += [PageTexte(num=2, texte=md2, nb_mots=40)]
            pages += [PageTexte(num=i, texte="", nb_mots=0) for i in range(3, 7)]
            pages.append(PageTexte(num=82, texte="7.1. Annexe n°1 – Arrêté préfectoral de demande d'autorisation de défrichement", nb_mots=12))
            an2 = annexes_embarquees(_doc("plan.pdf", "plan_gestion", pages))
            self.assertTrue(any(a["num"] == 1 for a in an2), an2)


class TestReconcileCorrections(unittest.TestCase):
    def test_ensembles_ug_vides_ignores(self):
        src_xl = Source(doc="ITK.xlsx", loc="H15")
        src_pdf = Source(doc="plan.pdf", loc="p.38")
        faits = [
            Fait(type="action_intitule", cle="SE2", valeur="Suivi hydraulique", source=src_xl),
            Fait(type="action_ug", cle="SE2", valeur=["UG1", "UG2"], source=src_xl),
            Fait(type="action_ug", cle="SE2", valeur=[], source=src_pdf, methode="llm"),
        ]
        cx = Ctx(faits, [], [], [])
        lignes, _ = _actions(cx, {})
        self.assertEqual(lignes[0].ugs.valeur, ["UG1", "UG2"])
        self.assertNotEqual(lignes[0].ugs.confiance, "basse")
        self.assertFalse(any("UG différentes" in q.texte for q in cx.q))

    def test_rename_ug_propage_dans_actions(self):
        src = Source(doc="ITK.xlsx", loc="H5")
        faits = [
            Fait(type="action_ug", cle="TU1", valeur=["UG1"], source=src),
            Fait(type="ug_libelle", cle="UG1", valeur="Landes", source=src),
        ]
        extra, renames, act_renames, ug_suppr, act_suppr = faits_depuis_corrections({
            "projet": {},
            "ugs": [{"id": "UG1", "origine": "ia", "supprime": False, "valeurs": {"code": "UG10"}}],
            "actions": [],
        })
        out = appliquer_aux_faits(faits, extra, renames, act_renames, ug_suppr, act_suppr)
        ugs = next(f.valeur for f in out if f.type == "action_ug")
        self.assertEqual(ugs, ["UG10"])
        self.assertTrue(any(f.cle == "UG10" for f in out if f.type == "ug_libelle"))


if __name__ == "__main__":
    unittest.main()
