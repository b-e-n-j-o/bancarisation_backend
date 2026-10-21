"""Lot 2 — planning, index, absence de montants, notice, non-régression des bilans."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from api.export.contexte import ExportContexte, ExportOptions
from api.export.modules.documents import LigneDocument, deposer_documents
from api.export.nommage import racine_zip
from api.export.pdf.echanges import groupes_notes
from api.export.pdf.index_html import page_index
from api.export.pdf.planning import decennies, ecrire_planning, pages_du_planning
from api.export.pdf.rendu import rendre_html


def _ctx(tmp: Path) -> ExportContexte:
    return ExportContexte(
        export_id=__import__("uuid").uuid4(),
        projet_id=__import__("uuid").uuid4(),
        user_id=__import__("uuid").uuid4(),
        organisation_id=__import__("uuid").uuid4(),
        options=ExportOptions(),
        dossier_tmp=tmp,
        url_front="https://bancarisation.kerelia.fr",
        racine_zip=racine_zip("REF", "abcdef1234567890", "Etang", "2026-10-21"),
    )


def test_decennies_sur_trente_ans():
    pages = decennies(2000, 2029)
    assert pages == [list(range(2000, 2010)), list(range(2010, 2020)), list(range(2020, 2030))]


def test_planning_regroupe_par_mesure():
    actions = [
        {"annee": annee, "statut": "planifie", "mesure": "C1 Prairie", "titre": "Fauchage", "fin": date(annee, 6, 30)}
        for annee in range(2000, 2030)
    ]
    pages = pages_du_planning(actions, date(2026, 6, 1))
    assert len(pages) == 3
    assert pages[0]["lignes"][0]["mesure"] == "C1 Prairie"
    assert pages[2]["lignes"][0]["cellules"][0]["couleur"] == "#b45309"


def test_planning_pdf_plusieurs_pages(tmp_path: Path):
    pytest = __import__("pytest")
    try:
        import weasyprint  # noqa: F401
        actions = [
            {"annee": annee, "statut": "planifie", "mesure": "C1", "titre": "Fauchage", "fin": date(annee, 12, 31)}
            for annee in range(2000, 2030)
        ]
        cible = tmp_path / "planning.pdf"
        ecrire_planning(cible, "Étang", "21/10/2026", actions)
    except (ImportError, OSError) as exc:
        pytest.skip(f"WeasyPrint indisponible ici : {exc}")
    from pypdf import PdfReader

    assert len(PdfReader(str(cible)).pages) >= 3


def test_fiche_sans_budget_n_a_pas_de_montant():
    html = rendre_html("fiche_projet.html.j2", {
        "titre": "Fiche",
        "projet_nom": "Étang",
        "genere_le": "21/10/2026",
        "page_size": "A4",
        "organisation": {"nom": "Org"},
        "partenaires": [],
        "mesures": [],
        "unites": [],
        "identite": [("Nom", "Étang")],
        "chiffres": [("Documents", 0)],
    })
    assert "€" not in html
    assert "Budget prévu" not in html


def test_index_liens_existent(tmp_path: Path):
    (tmp_path / "01_Projet").mkdir()
    (tmp_path / "01_Projet" / "projet.json").write_text("{}", encoding="utf-8")
    (tmp_path / "00_LISEZMOI.pdf").write_bytes(b"%PDF")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    html = page_index("Étang", "Org", "21/10/2026", {
        "01_Projet": [{"chemin": "01_Projet/projet.json", "nom": "projet.json", "description": "Fiche"}],
    }, [])
    (tmp_path / "index.html").write_text(html, encoding="utf-8")
    import re
    for href in re.findall(r'href="([^"]+)"', html):
        assert (tmp_path / href).exists(), href


def test_document_deja_range_n_est_pas_recopie(tmp_path: Path):
    ctx = _ctx(tmp_path)
    appels = []

    def telecharger(chemin: str, _cible: Path) -> str:
        appels.append(chemin)
        return "abc"

    deposer_documents(
        ctx,
        [LigneDocument(
            id="bilan",
            nom="Bilan.pdf",
            nom_fichier="bilan.pdf",
            bucket_path="secret/bilan.pdf",
            categorie="financier",
            date_document=date(2026, 12, 31),
            created_at=None,
            taille_octets=10,
            deposant=None,
            occurrence_id=None,
            liens="",
            chemin_ailleurs="10_Bilans/financiers/2026_bilan-financier_VALIDE.pdf",
        )],
        telecharger,
        True,
    )
    assert appels == []
    assert ctx.alias_fichiers["bilan"].startswith("10_Bilans/")


def test_notes_vides_et_globales():
    assert groupes_notes([]) == []
    groupes = groupes_notes([
        {"action_id": None, "contenu": "Globale", "mesure": ""},
        {"action_id": "a1", "contenu": "Locale", "mesure": "C1"},
    ])
    assert groupes[0]["titre"] == "C1"
    assert groupes[1]["titre"] == "Notes du projet"


def test_template_bilan_inchange():
    texte = Path("api/budget/rapport/templates/bilan_annuel.html.j2").read_text(encoding="utf-8")
    assert "Export KerERC" not in texte
    assert "Version {{ meta.version }}" in texte
