"""Tests hors ligne.   ARX_TEST_PDF=/chemin/arrete.pdf pytest -q tests/"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from arx.config import Settings
from arx.llm import MistralClient
from arx.s2_segment import fr_date
from arx.s4_verify import dedupe_and_contradictions
from arx.s5_schedule import _add, schedule
from arx.schemas import Echeance, FicheLLM, Obligation

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "examples" / "bruges" / "expected_llm.json"
PDF = os.environ.get("ARX_TEST_PDF")
needs_pdf = pytest.mark.skipif(not PDF or not Path(PDF).exists(), reason="ARX_TEST_PDF non défini")


def ob(article, intitule, **ech):
    return Obligation(intitule=intitule, categorie="transmission", article=article, echeance=Echeance(**ech),
                      preuve_attendue=["cr_suivi_ecologique"])


# ------------------------------------------------------------------ unitaires
def test_dates():
    assert fr_date("Bordeaux, le 20 novembre 2025") == "2025-11-20"
    assert fr_date("le 1er octobre 2025") == "2025-10-01"
    from datetime import date
    assert _add(date(2025, 11, 30), 3, "mois", 1) == date(2026, 2, 28)
    assert _add(date(2025, 12, 5), 15, "jour", -1) == date(2025, 11, 20)


def test_contradiction_rule_and_merge():
    a = ob("14", "Transmettre le compte-rendu des suivis", kind="date_annuelle", day_month="03-31", year_offset=1)
    b = ob("16.1", "Transmettre le compte-rendu des suivis", kind="date_annuelle", day_month="12-31", year_offset=0)
    c = ob("9", "Transmettre le compte-rendu des suivis", kind="permanente")
    kept, ctl = dedupe_and_contradictions([a, b, c])
    assert len(ctl) == 1 and set(ctl[0].articles) == {"14", "16.1"}
    assert any("9" in o.articles_lies for o in kept)          # redite sans échéance : fusionnée


def test_schedule():
    o = ob("12.4", "Plan de gestion", kind="delai_apres", anchor="notification", offset_value=6, offset_unit="mois")
    o.id = "OBL-1"
    b = ob("16.1", "Bilans", kind="annees_relatives", anchor="demarrage_travaux", years_relative=[4, 6], day_month="03-31")
    b.id = "OBL-2"
    rows, anchors = schedule([o, b], FicheLLM(date_signature="2025-11-20"), {"demarrage_travaux": "2025-12-05"})
    r1 = next(r for r in rows if r.obligation_id == "OBL-1")
    assert r1.date == "2026-05-20" and "supposée" in r1.hypothese
    assert next(r for r in rows if r.obligation_id == "OBL-2").dates == ["2029-03-31", "2031-03-31"]


# ------------------------------------------------------------------ sur le PDF
@needs_pdf
def test_segmentation():
    from arx.s1_ingest import ingest
    from arx.s2_segment import segment
    s = Settings(offline=True, runs_dir=Path("/tmp/arx_test"))
    s.runs_dir.mkdir(exist_ok=True)
    g = segment(ingest(Path(PDF), s, MistralClient(s, s.runs_dir))[0])
    nums = [a.numero for a in g.articles]
    assert nums[:3] == ["1", "2", "3"] and "11.8.1" in nums and "16.3" in nums and nums[-1] == "21"
    assert next(a for a in g.articles if a.numero == "11.8.1").parent == "11.8"
    assert len(g.visas) == 13 and len(g.considerants) == 6
    assert g.signature_date == "2025-11-20" and "149" in g.reference
    assert [f.numero for f in g.figures] == [str(i) for i in range(1, 10)]
    assert not g.issues


@needs_pdf
def test_full_run_injected(tmp_path):
    from arx.pipeline import run
    res, run_dir = run(Path(PDF), Settings(offline=True, runs_dir=tmp_path), anchors={"demarrage_travaux": "2025-12-05"},
                       inject=EXPECTED, log=lambda *_: None)
    assert res.synthese.nb_especes == 35
    assert all(c.verified for o in res.obligations for c in o.citations)               # citations retrouvées
    alerts = [c for c in res.controles if c.gravite == "alerte"]
    assert {frozenset(c.articles) for c in alerts} == {frozenset({"14", "16.1"}), frozenset({"12.1", "16.1"})}
    pg = next(r for r in res.echeancier if "plan de gestion" in r.intitule.lower())
    assert pg.date == "2026-05-20"
    assert (run_dir / "rapport.md").exists() and (run_dir / "arrete.json").exists()


@needs_pdf
def test_llm_plumbing_with_fake_client(tmp_path, monkeypatch):
    """Le vrai chemin LLM (k=2 échantillons, consolidation, contrôles) avec un faux client."""
    from arx.pipeline import run
    from arx import schemas
    exp = json.loads(EXPECTED.read_text(encoding="utf-8"))

    def fake(self, model, messages, schema, temperature, n, cache_key_hint):
        user = messages[-1]["content"]
        if schema is schemas.FicheLLM:
            return [json.dumps(exp["fiche"], ensure_ascii=False)] * n, {}
        if schema is schemas.SyntheseLLM:
            return [json.dumps(exp["synthese"], ensure_ascii=False)] * n, {}
        if schema is schemas.ArticleLLM:
            num = re.search(r"ANALYSE DE L'ARTICLE ([\d.]+)", user).group(1)
            art = exp["articles"].get(num, {"resume": "", "categories": []})
            second = json.loads(json.dumps(art))
            second["obligations"] = second.get("obligations", [])[:-1]     # 2e échantillon : une obligation en moins
            return [json.dumps(art, ensure_ascii=False), json.dumps(second, ensure_ascii=False)][:n], {}
        if schema is schemas.ControlesLLM:
            return [json.dumps({"controles": [{"sujet": "Bilans : « 3 et 5 ans » contre N+4 / N+6", "description": "art. 14 vs 16.1",
                                               "articles": ["14", "16.1"], "valeurs": ["3 et 5 ans", "N+4, N+6"],
                                               "citations": [{"page": 16, "quote": "bilans effectués à 3 et 5 ans"}]}]})], {}
        raise AssertionError(schema)

    monkeypatch.setattr(MistralClient, "_complete", fake)
    res, run_dir = run(Path(PDF), Settings(api_key="x", runs_dir=tmp_path, samples_k=2), log=lambda *_: None)
    assert any(o.support == 0.5 for o in res.obligations)                 # obligation vue dans 1 échantillon sur 2
    assert any(o.decision != "retenue" for o in res.obligations)
    llm_ctl = [c for c in res.controles if c.origine == "contradiction_llm"]
    assert llm_ctl and llm_ctl[0].citations[0].verified
    assert (run_dir / "llm_calls.jsonl").exists()
