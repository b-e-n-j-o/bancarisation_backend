"""Fiche arrêté : citations vérifiées, concordance regex/LLM, dédoublonnage.
python -m tests.test_arrete   (depuis backend/api/ocr/ingest_erc)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest_erc.arrete import (  # noqa: E402
    Champ, Citation, Duree, FicheArrete, Identite, MesureArrete, Prescription,
    faits_depuis_fiche, verifier_arrete,
)
from ingest_erc.modeles import Document, Fait, PageTexte, Source  # noqa: E402
from ingest_erc.reconcile_ref import reconcilier  # noqa: E402

TEXTE = """\
ARRÊTÉ portant dérogation aux interdictions de destruction de spécimens
d'espèces animales protégées et de leurs habitats
Réf. DBEC : n° 90/2021
Fait à Bordeaux, le 15 mars 2021
Le bénéficiaire de la dérogation est le Département de la Gironde.
Les mesures de compensation sont prescrites pour une durée minimum de 20 ans.
La mesure MC1 consiste en la restauration de landes humides
sur 4,72 ha minimum, répartis sur 3 parcelles.
Un bilan annuel est transmis à la DREAL avant le 31 mars.
"""


def _doc() -> Document:
    p = PageTexte(num=1, texte=TEXTE, nb_mots=80)
    return Document(nom="arrete.pdf", chemin="", sha256="test", extension=".pdf",
                    role="arrete", pages=[p])


def _cit(extrait: str, page: int = 1) -> Citation:
    return Citation(page=page, texte=extrait)


def _fiche_hallucinee() -> FicheArrete:
    return FicheArrete(
        identite=Identite(
            reference=Champ(valeur="90/2021",
                            citation=_cit("Réf. DBEC : n° 90/2021")),
            date=Champ(valeur="2021-03-15",
                       citation=_cit("Fait à Bordeaux, le 15 mars 2021")),
            autorite=Champ(valeur="Préfet de l'Ardèche",
                           citation=_cit("Vu le code de l'urbanisme article L123")),
            beneficiaire=Champ(valeur="Département de la Gironde",
                               citation=_cit("bénéficiaire de la dérogation est le Département")),
            procedure=Champ(valeur="dérogation espèces protégées",
                            citation=_cit("dérogation aux interdictions de destruction")),
        ),
        duree=Duree(duree_ans=30, citation=_cit("pour une durée de trente ans")),
        mesures=[
            MesureArrete(code="MC1", libelle="restauration de landes", type_erc="C",
                         surface_ha=4.72, surface_texte="4,72 ha", nb_parcelles=3,
                         citation=_cit("landes humides sur 4,72 ha minimum, répartis")),
            MesureArrete(code="MC2", libelle="mesure inventée", type_erc="C",
                         surface_ha=12.0, surface_texte="12 ha",
                         citation=_cit("la mesure MC2 inventée n'existe nulle part")),
        ],
        prescriptions=[
            Prescription(id="P1", categorie="bilan_transmission",
                         texte="bilan annuel transmis à la DREAL",
                         citation=_cit("Un bilan annuel est transmis à la DREAL")),
        ],
    )


def _src(loc="p.1", extrait=None) -> Source:
    return Source(doc="arrete.pdf", loc=loc, extrait=extrait)


def _fait(type_, valeur, methode="deterministe", cle="projet"):
    return Fait(type=type_, cle=cle, valeur=valeur, source=_src(), methode=methode)


def test_verifier_ecarte_autorite_et_mc2():
    doc = _doc()
    fiche = verifier_arrete(_fiche_hallucinee(), doc)
    ecartes = " ".join(fiche.verification["ecartes"])
    assert fiche.identite.autorite.valeur is None, fiche.identite.autorite
    assert "identité.autorite" in ecartes
    assert [m.code for m in fiche.mesures] == ["MC1"]
    assert "MC2" in ecartes
    assert fiche.prescriptions and fiche.prescriptions[0].id == "P1"
    assert fiche.duree.duree_ans is None          # citation « trente ans » absente du texte
    print("vérification : autorité et MC2 écartées,", len(fiche.verification["ecartes"]), "écart(s)")


def test_concordance_dedup_prescriptions():
    doc = _doc()
    fiche = verifier_arrete(_fiche_hallucinee(), doc)
    # durée divergente : regex 20 ans, LLM 30 ans (après vérif la durée LLM est déjà écartée ;
    # on réinjecte un fait llm pour simuler une citation valide « trente ans »).
    faits = [
        _fait("decision_reference", "DBEC 90/2021"),
        _fait("decision_date", "2021-03-15"),
        _fait("maitre_ouvrage", "Département de la Gironde"),
        _fait("duree_ans", 20),
        _fait("obligation_surface",
              {"texte": "restauration de landes", "surface_min_ha": 4.72, "nb_parcelles": 3},
              cle="MC1"),
        _fait("decision_reference", "90/2021", "llm"),
        _fait("decision_date", "2021-03-15", "llm"),
        _fait("maitre_ouvrage", "le Département de la Gironde", "llm"),
        _fait("duree_ans", 30, "llm"),
        _fait("obligation_surface",
              {"texte": "restauration de landes", "surface_min_ha": 4.72, "nb_parcelles": 3},
              "llm", cle="MC1"),
    ]
    faits += faits_depuis_fiche(fiche, doc)
    ref = reconcilier(faits, [], [], [doc])
    assert ref.projet["reference_decision"].confiance == "haute"
    assert ref.projet["date_decision"].confiance == "haute"
    assert ref.projet["maitre_ouvrage"].confiance == "haute"
    divergences = [q.id for q in ref.questions if q.id.startswith("arrete_divergence:")]
    assert "arrete_divergence:duree_ans" in divergences, divergences
    assert len(ref.obligations_surfaciques) == 1
    assert "projet" in ref.decisions["arrete"]
    presc = ref.decisions["arrete"]["projet"]["prescriptions"]
    assert any(p["id"] == "P1" for p in presc), presc
    print("réconciliation : concordance haute, 1 obligation, prescription dans decisions")


if __name__ == "__main__":
    test_verifier_ecarte_autorite_et_mc2()
    test_concordance_dedup_prescriptions()
    print("OK")
