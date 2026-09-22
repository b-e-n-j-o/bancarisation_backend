"""Tri des remarques du LLM (exemples issus d'un run réel sur un autre dossier).
python -m tests.test_remarques   (depuis backend/api/ocr/ingest_erc)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest_erc.carte import CarteDocument, Remarque, Vocabulaire
from ingest_erc.modeles import Cellule, Document, Question, ReferentielPropose
from ingest_erc.ocr import pages_depuis_markdown
from ingest_erc.remarques import trier

REMARQUES = [
    ("Incohérence dans le numéro d'arrêté : cité « n° 2018D/718 » en page 1 et 4, mais "
     "« n°2022/05/12-062 » en pages 2, 6, 40 et 30", 1, "n° 2018D/718"),
    ("Incohérence sur l'année de fin du plan de gestion : citée « 2049 » en page 4, « 2048 » "
     "sur les frises des fiches et « 2058 » dans la fiche MG 2 page 32", 4, "2049"),
    ("Le tableau des coûts page 33 contient des coquilles d'OCR (« Planage et coordination » "
     "au lieu de « Pilotage et coordination »)", 33, "Planage et coordination"),
    ("La fiche TE 1 indique « Objectif opérationnel : A2 » en page 24 alors que le tableau de "
     "synthèse page 13 l'associe à A1", 24, "Objectif opérationnel : A2"),
]


def document():
    pages = ["n° 2018D/718", "x", "x", "2049", "x", "x", "x", "x", "x", "x", "x", "x", "x",
             "x", "x", "x", "x", "x", "x", "x", "x", "x", "x", "Objectif opérationnel : A2",
             "x", "x", "x", "x", "x", "x", "x", "x", "Planage et coordination"]
    p = pages_depuis_markdown(pages)
    p[32].fiabilite_ocr = 0.6          # page de coûts mal relue
    return Document(nom="pg.pdf", chemin="", sha256="x", extension=".pdf", role="plan_gestion", pages=p)


def test_tri():
    doc = document()
    carte = CarteDocument(vocabulaire=Vocabulaire(unite_terme="UG", unite_prefixes=["UG"]),
                          remarques=[Remarque(texte=t, page=pg, citation=c) for t, pg, c in REMARQUES])
    for r in carte.remarques:                 # vérification déjà faite par carte.verifier()
        r.verifiee = True
    ref = ReferentielPropose(
        projet={"annee_fin": Cellule(valeur=2048, confiance="moyenne")},
        ugs=[], actions=[],
        questions=[Question(id="Q1", portee="projet", texte="état zéro ?")],
        controles=[], stats={"questions": 1})

    res = trier(carte, doc, ref)
    assert res["questions"] == 2                                     # arrêté + année de fin
    q = {x.portee: x for x in ref.questions if x.id.startswith("R")}
    assert set(q) == {"reference_decision", "annee_fin"}
    assert q["annee_fin"].options == ["2049", "2048", "2058"] and q["annee_fin"].proposition == "2048"
    assert [a["texte"][:12] for a in ref.anomalies] == ["La fiche TE "]   # erreur du dossier
    assert len(res["internes"]) == 1 and res["internes"][0]["type"] == "qualite_de_lecture"
    assert ref.stats["anomalies"] == 1 and ref.stats["questions"] == 3
    print("OK :", res["questions"], "question(s),", len(ref.anomalies), "anomalie(s),",
          len(res["internes"]), "interne(s)")


if __name__ == "__main__":
    test_tri()
