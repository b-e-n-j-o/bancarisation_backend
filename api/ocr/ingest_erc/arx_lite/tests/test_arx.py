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


def _fake_factory(exp, delay=0.0, calls=None):
    import time
    from arx import schemas

    def fake(self, model, messages, schema, temperature, n, cache_key_hint):
        if calls is not None:
            calls.append(schema.__name__)
        time.sleep(delay)
        user = messages[-1]["content"]
        dump = lambda o: json.dumps(o, ensure_ascii=False)  # noqa: E731
        if schema is schemas.FicheLLM:
            return [dump(exp["fiche"])] * n, {}
        if schema is schemas.SyntheseLLM:
            return [dump(exp["synthese"])] * n, {}
        if schema is schemas.ArticlesBatchLLM:
            nums = re.search(r"ANALYSE DES ARTICLES ([\d., ]+)", user).group(1).replace(" ", "").split(",")
            arts = [{**exp["articles"][x], "numero": x} for x in nums if x in exp["articles"]]
            if n > 1:   # 2e échantillon : une obligation de moins dans le 1er article qui en a
                second = json.loads(dump(arts))
                for a in second:
                    if a.get("obligations"):
                        a["obligations"] = a["obligations"][:-1]
                        break
                return [dump({"articles": arts}), dump({"articles": second})][:n], {}
            return [dump({"articles": arts})] * n, {}
        if schema is schemas.SinglePassLLM:
            arts = [{**v, "numero": k} for k, v in exp["articles"].items()]
            return [dump({"fiche": exp["fiche"], "synthese": exp["synthese"], "articles": arts})] * n, {}
        if schema is schemas.ControlesLLM:
            return [dump({"controles": [{"sujet": "Bilans : « 3 et 5 ans » contre N+4 / N+6", "description": "art. 14 vs 16.1",
                                         "articles": ["14", "16.1"], "valeurs": ["3 et 5 ans", "N+4, N+6"],
                                         "citations": [{"page": 16, "quote": "bilans effectués à 3 et 5 ans"}]}]})], {}
        raise AssertionError(schema)
    return fake


@needs_pdf
def test_parallel_mode_few_calls_and_fast(tmp_path, monkeypatch):
    """Mode par défaut : fiche + synthèse + 3 lots, en parallèle. Avec 1 s simulée par appel, ~1 s au total."""
    import time
    from arx.pipeline import run
    exp = json.loads(EXPECTED.read_text(encoding="utf-8"))
    calls: list = []
    monkeypatch.setattr(MistralClient, "_complete", _fake_factory(exp, delay=1.0, calls=calls))
    t0 = time.time()
    res, _ = run(Path(PDF), Settings(api_key="x", runs_dir=tmp_path), log=lambda *_: None)
    elapsed = time.time() - t0
    assert res.stats["mode"] == "parallel" and len(calls) == res.stats["appels_llm_extraction"] <= 6
    assert elapsed < 2.5, elapsed
    assert len(res.obligations) == 99 and not any(c.sujet.startswith("Articles non analysés") for c in res.controles)


@needs_pdf
def test_single_mode_and_options(tmp_path, monkeypatch):
    from arx.pipeline import run
    exp = json.loads(EXPECTED.read_text(encoding="utf-8"))
    calls: list = []
    monkeypatch.setattr(MistralClient, "_complete", _fake_factory(exp, calls=calls))
    res, _ = run(Path(PDF), Settings(api_key="x", runs_dir=tmp_path / "a", mode="single"), log=lambda *_: None)
    assert calls == ["SinglePassLLM"] and len(res.obligations) == 99
    # options : 2 échantillons + passe de contrôle LLM
    res, _ = run(Path(PDF), Settings(api_key="x", runs_dir=tmp_path / "b", samples_k=2, contradiction_pass=True),
                 log=lambda *_: None)
    assert any(o.support == 0.5 for o in res.obligations)
    llm_ctl = [c for c in res.controles if c.origine == "contradiction_llm"]
    assert llm_ctl and llm_ctl[0].citations[0].verified
