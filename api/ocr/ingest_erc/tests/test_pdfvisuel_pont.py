"""Ancre PDF, plannings géométriques et fenêtres multiples.
python -m tests.test_pdfvisuel_pont   (depuis backend/api/ocr/ingest_erc)
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest_erc.carte import Regles
from ingest_erc.extract_referentiel import _ancre_depuis_lignes
from ingest_erc.mdnorm import markdown_vers_texte
from ingest_erc.modeles import ActionVerrouillee, PageTexte, ReferentielVerrouille
from ingest_erc.pdf_sections import decouper_fiches
from ingest_erc.pdfvisuel_pont import faits_ancre, lectures_fenetres, lectures_plannings
from ingest_erc.passe3 import (
    Contexte, FaitTemporel, Fenetre, Source, Temporalite,
    construire_calendrier, extraire_grilles_pdf, rattacher, vers_echeances,
)
from ingest_erc.reconcile_ref import Ctx, _projet
from ingest_erc.extract_referentiel import _f

PROFIL = (ROOT / "analyse_pdf/pdfvisuel_v1_1/exemple_riviere/profil.json")


def _doc():
    return SimpleNamespace(nom="Plan_de_gestion_de_ddep.pdf", role="plan_gestion",
                          pages=[], lignes_repetees=[])


def test_bruges_annee_n_2025():
    profil = json.loads(PROFIL.read_text())
    doc = _doc()
    faits = faits_ancre(doc, profil)
    assert len(faits) == 1, faits
    assert faits[0].valeur["N"] == 2025
    assert faits[0].valeur["etat_zero"] is None
    cx = Ctx(faits, [], [], [doc])
    projet = _projet(cx)
    assert projet["annee_N"].valeur == 2025
    assert projet["annee_N"].confiance == "haute"
    assert "annee_etat_zero" not in projet
    assert not any(q.regle == "ancre_annee_N" for q in cx.q)


def test_etat_zero_le_barp_inchange():
    doc = SimpleNamespace(nom="ITK.xlsx", role="tableur", pages=[], lignes_repetees=[])
    faits = [_f("ancre_temporelle", "projet", {"etat_zero": 2022, "N": 2023},
                "ITK.xlsx", "Synthèse!L2", "État zéro 2022 / N = 2023")]
    projet = _projet(Ctx(faits, [], [], [doc]))
    assert projet["annee_etat_zero"].valeur == 2022
    assert projet["annee_N"].valeur == 2023


def test_conflit_de_N_pose_une_question():
    faits = [
        _f("ancre_temporelle", "projet", {"etat_zero": None, "N": 2025}, "a.pdf", "p.1", "N=2025"),
        _f("ancre_temporelle", "projet", {"etat_zero": None, "N": 2024}, "b.pdf", "p.2", "N=2024"),
    ]
    cx = Ctx(faits, [], [], [])
    projet = _projet(cx)
    assert any(q.regle == "ancre_annee_N" for q in cx.q)
    assert projet["annee_N"].confiance == "basse"


def test_rangs_avant_annees():
    rows = [
        ["", "N", "N+1", "N+2", "N+3", "N+4", "N+5"],
        ["", "2025", "2026", "2027", "2028", "2029", "2030"],
    ]
    assert _ancre_depuis_lignes(rows) == {"etat_zero": 2024, "N": 2025}
    rows_ez = [
        ["2022", "2023", "2024", "2025", "2026", "2027"],
        ["Etat zéro", "N", "N+1", "N+2", "N+3", "N+4"],
    ]
    assert _ancre_depuis_lignes(rows_ez)["N"] == 2023


def test_bruges_croix_sans_bande_et_fenetre_es2():
    profil = json.loads(PROFIL.read_text())
    # Le profil d'exemple n'a pas encore le contexte v1.2 : on le pose pour ES2 / RG1.
    for page in profil["pages"]:
        for g in page["grilles"]:
            if g["type_axe"] == "mois" and g["page"] == 30:
                g["contexte"] = {"code_proche": "RG1"}
            if g["type_axe"] == "mois" and g["page"] == 37:
                g["contexte"] = {"code_proche": "ES2"}
    lus = {x["code"]: x["rangs"] for x in lectures_plannings(profil)}
    assert lus["ES1"] == [0, 1, 2, 3, 4, 5]
    assert lus["ES2"] == [0, 1, 2, 3, 4, 5]
    assert all("bande" not in str(x) or x["rangs"] == lus[x["code"]] for x in lectures_plannings(profil))
    es1 = next(x for x in lectures_plannings(profil) if x["code"] == "ES1")
    assert any("bande" in a for a in es1["avertissements"])
    fens = {x["code"]: x["plages"] for x in lectures_fenetres(profil)}
    assert fens["RG1"] == [(3, 4), (7, 8)]
    assert fens["ES2"] == [(4, 6)]


def _contexte():
    ref = ReferentielVerrouille(
        projet={"annee_N": 2025, "annee_fin": 2055},
        ugs=[],
        actions=[
            ActionVerrouillee(code="ES1", intitule="Suivi du Lotier"),
            ActionVerrouillee(code="ES2", intitule="Suivi avifaune"),
            ActionVerrouillee(code="RG1", intitule="Réouverture"),
            ActionVerrouillee(code="TU1", intitule="Travaux"),
        ],
        empreinte="t", verrouille_le="2026-01-01",
    )
    return Contexte(ref, Regles.defaut())


def test_calendrier_tableur_prime_sur_le_pdf():
    cx = _contexte()
    src = Source(doc="plan.pdf", loc="p.1")
    faits = [
        FaitTemporel(code="TU1", role_source="planning_passages",
                     temporalite=Temporalite(forme="annees", annees=[2022, 2023]), source=src),
        FaitTemporel(code="TU1", role_source="planning_pdf",
                     temporalite=Temporalite(forme="annees", annees=[2025]), source=src),
    ]
    plans = [p for p in construire_calendrier(faits, cx) if p.code == "TU1" and p.temporalite.annees]
    assert plans[0].temporalite.annees == [2022, 2023]


def test_pdf_seul_deux_fenetres_et_pas_de_rejet_ancre():
    cx = _contexte()
    src = Source(doc="plan.pdf", loc="p.30 grille p030_g1")
    faits = [
        FaitTemporel(code="RG1", role_source="planning_pdf",
                     temporalite=Temporalite(forme="rangs", rangs=[0]), source=src,
                     confiance_extraction=0.9),
        FaitTemporel(code="RG1", role_source="fenetre_pdf",
                     temporalite=Temporalite(forme="inconnu", fenetres=[
                         Fenetre(mois_debut=3, mois_fin=4), Fenetre(mois_debut=7, mois_fin=8)]),
                     source=src),
    ]
    ok, rejets = rattacher(faits, cx)
    assert not any(r.motif == "rang_sans_ancre" for r in rejets)
    plans = construire_calendrier(ok, cx)
    echeances = [e for e in vers_echeances(plans, cx) if e.code_operation == "RG1" and e.recurrence.annees]
    debuts = sorted(e.fenetre_intervention.debut for e in echeances)
    assert debuts == ["03-01", "07-01"]
    assert echeances[0].recurrence.annees == [2025]


def test_rattachement_ne_vole_pas_le_titre():
    pages = [PageTexte(num=2, texte="\n".join([
        "Rattachement probable : code « RG1 ».",
        "Rattachement probable : « RG1 : Réouverture du milieu pour le lotier ».",
        "# RG1 : Réouverture du milieu pour le lotier",
        "Le reste de la fiche.",
    ]), nb_mots=20)]
    carte = SimpleNamespace(
        fiches=[SimpleNamespace(code="RG1", titre="Réouverture du milieu pour le lotier", page=2)],
        sections=[],
    )
    secs = decouper_fiches(pages, carte, Regles.defaut())
    assert secs
    assert secs[0].lignes[0][1].startswith("# RG1")


def test_lecture_survit_a_laplatissement():
    md = "\n".join([
        "[GRILLE p030_g1 — page 30 — axe mensuel]",
        "| Ligne | Mars |",
        "| --- | --- |",
        "| x | ■ |",
        "",
        "Lecture :",
        "- « Période d'intervention » : cellules colorées en mars et avril.",
    ])
    plat = markdown_vers_texte(md)
    assert "Lecture :" in plat
    assert "cellules colorées en mars et avril" in plat


def test_grilles_bruges_depuis_cache(tmp_path=None):
    import tempfile
    profil = json.loads(PROFIL.read_text())
    for page in profil["pages"]:
        for g in page["grilles"]:
            if g["type_axe"] == "mois" and g["page"] == 37:
                g["contexte"] = {"code_proche": "ES2"}
    cache = Path(tempfile.mkdtemp())
    (cache / "abc.pdfvisuel.json").write_text(json.dumps(profil))
    doc = SimpleNamespace(nom="Plan_de_gestion_de_ddep.pdf", role="plan_gestion",
                          chemin="/tmp/absent.pdf", sha256="abc", extension=".pdf", pages=[])
    faits, rejets = extraire_grilles_pdf(doc, _contexte(), cache)
    assert not rejets
    es2 = [f for f in faits if f.code == "ES2" and f.role_source == "planning_pdf"]
    assert es2 and es2[0].temporalite.rangs == [0, 1, 2, 3, 4, 5]
    fen = [f for f in faits if f.code == "ES2" and f.role_source == "fenetre_pdf"]
    assert fen and (fen[0].temporalite.fenetres[0].mois_debut, fen[0].temporalite.fenetres[0].mois_fin) == (4, 6)


if __name__ == "__main__":
    test_bruges_annee_n_2025()
    test_etat_zero_le_barp_inchange()
    test_conflit_de_N_pose_une_question()
    test_rangs_avant_annees()
    test_bruges_croix_sans_bande_et_fenetre_es2()
    test_calendrier_tableur_prime_sur_le_pdf()
    test_pdf_seul_deux_fenetres_et_pas_de_rejet_ancre()
    test_rattachement_ne_vole_pas_le_titre()
    test_lecture_survit_a_laplatissement()
    test_grilles_bruges_depuis_cache()
    print("ok")
