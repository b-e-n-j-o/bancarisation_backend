"""La carte du document (sortie LLM simulée) pilote l'extraction déterministe.
python -m tests.test_carte   (depuis backend/api/ocr/ingest_erc)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest_erc.carte import CarteDocument, ColonnesTableau, Regles, compacter, estimer_tokens, verifier
from ingest_erc.extract_referentiel import faits_plan, faits_tableaux_md
from ingest_erc.mdnorm import retirer_entetes
from ingest_erc.modeles import Document
from ingest_erc.ocr import pages_depuis_markdown
from ingest_erc.reconcile_ref import reconcilier
from tests.test_mistral_md import document as document_le_barp

FX = Path(__file__).parent / "fixtures_carte"


def document_cen():
    mds = [(FX / "cen" / f"p{i}.md").read_text() for i in range(1, 6)]
    pages = pages_depuis_markdown(mds)
    nettoyes = retirer_entetes([p.texte for p in pages])
    rep = list(retirer_entetes.dernier_bruit)
    for p, t in zip(pages, nettoyes):
        p.texte = t
    return Document(nom="plan_cen.pdf", chemin="", sha256="cen", extension=".pdf",
                    role="plan_gestion", pages=pages, lignes_repetees=rep)


def carte(nom, doc):
    return verifier(CarteDocument.model_validate_json((FX / nom).read_text()), doc)


def resume(faits):
    ug = {}
    for f in faits:
        if f.type == "action_ug":
            ug.setdefault(f.cle, set()).update(f.valeur)
    return {k: sorted(v) for k, v in sorted(ug.items())}


def test_le_barp():
    d = document_le_barp()
    c = carte("carte_le_barp.json", d)
    v = c.verification
    assert v["fiches"] == [2, 3] and any("TU 7" in e for e in v["ecartes"])        # fiche inventée écartée
    assert v["tableaux"] == [3, 4] and any("p.44" in e for e in v["ecartes"])     # tableau inventé écarté
    r = Regles.depuis(c)
    f_plan, sel = faits_plan(d, c, r)
    assert sel["fiches_localisees"] == "2/2"
    assert resume(f_plan) == {"TU1": ["UG1"], "TU5": ["UG6"]}
    f_md = faits_tableaux_md(d, sel["familles"], c, r)
    ug = resume(f_md)
    assert len(ug) == 13 and ug["TE2"] == ["UG2", "UG4", "UG6"]
    assert [f.valeur["parcelle"] for f in f_md if f.type == "parcelle_tableau"] == ["C480c", "C444b", "C480b", "C480a", "D2a"]
    assert {f.cle for f in f_md if f.type == "mesure_plan"} == {"EV-1", "EV-2", "EV-3", "C1", "C2", "C3"}
    assert any(f.type == "ancre_temporelle" and f.methode == "llm" for f in f_plan)
    print("Le Barp :", estimer_tokens(compacter(d)), "tokens envoyés (fixtures)")


def test_cen_sans_carte():
    from unittest.mock import patch
    d = document_cen()
    with patch("ingest_erc.extract_referentiel.llm.actif", return_value=False), \
         patch("ingest_erc.pdf_sections.llm.actif", return_value=False):
        f_plan, _ = faits_plan(d)
        f_md = faits_tableaux_md(d, {})
    assert resume(f_plan + f_md) == {}          # les règles « Le Barp » ne voient rien
    print("CEN sans carte : 0 lien action ↔ unité")


def test_cen_avec_carte():
    d = document_cen()
    c = carte("carte_cen.json", d)
    assert c.verification["fiches"] == [3, 3] and c.verification["tableaux"] == [2, 2]
    r = Regles.depuis(c)
    f_plan, sel = faits_plan(d, c, r)
    f_md = faits_tableaux_md(d, sel["familles"], c, r)
    assert resume(f_plan) == {"GH1": ["S1", "S2"], "GH2": ["S3"], "SE1": ["S1", "S2", "S3"]}
    assert resume(f_md) == resume(f_plan)                                       # 2 sources concordantes
    libs = {f.cle: f.valeur for f in f_md if f.type == "ug_libelle"}
    assert libs == {"S1": "Prairie de fauche humide", "S2": "Mégaphorbiaie", "S3": "Prairie pâturée"}
    assert any(f.type == "famille_code" and f.cle == "GH" for f in f_plan)
    ref = reconcilier(f_plan + f_md, [], [], [d])
    assert [a.code for a in ref.actions] == ["GH1", "GH2", "SE1"]
    assert all(a.ugs.confiance == "haute" for a in ref.actions)
    assert ref.projet["annee_etat_zero"].valeur == 2025
    print("CEN avec carte :", resume(f_plan), "—", len(ref.questions), "question(s)")


def test_colonnes_null_n_ecarte_pas_la_carte():
    """Régression : GLM envoie colonnes=null → la carte entière était jetée."""
    import json
    raw = json.loads((FX / "carte_le_barp.json").read_text())
    assert raw["tableaux"], "fixture sans tableaux"
    raw["tableaux"][0]["colonnes"] = None
    raw["tableaux"].append({"role": "planning", "page": 50, "colonnes": None})
    c = CarteDocument.model_validate(raw)
    assert c.tableaux[0].colonnes.model_dump() == ColonnesTableau().model_dump()
    assert c.tableaux[-1].role == "planning" and c.tableaux[-1].colonnes is not None
    print("colonnes=null : carte conservée,", len(c.tableaux), "tableaux")


if __name__ == "__main__":
    test_le_barp()
    test_cen_sans_carte()
    test_cen_avec_carte()
    test_colonnes_null_n_ecarte_pas_la_carte()
    print("OK")
