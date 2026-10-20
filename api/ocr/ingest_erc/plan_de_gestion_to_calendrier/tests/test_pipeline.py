"""Tests hors ligne (aucun appel API).

    PGCAL_TEST_PDF=/chemin/Plan_de_gestion_de_ddep.pdf pytest -q tests/

Sans la variable, seuls les tests qui n'ont pas besoin du PDF tournent.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from pgcal.config import Settings
from pgcal.s3_grids import parse_label
from pgcal.s6_expand import expand, segment_years
from pgcal.schemas import Frame, Rule, SegmentLLM

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "gold" / "bruges_rules.json"
PDF = os.environ.get("PGCAL_TEST_PDF")
needs_pdf = pytest.mark.skipif(not PDF or not Path(PDF).exists(), reason="PGCAL_TEST_PDF non défini")


# ----------------------------------------------------------------- unitaires
def test_parse_label():
    assert parse_label("N+6 à N+10 2031 à 2035") == {"rel_start": 6, "rel_end": 10, "year_start": 2031,
                                                       "year_end": 2035, "axis": "rel_year"}
    assert parse_label("N") ["rel_start"] == 0
    assert parse_label("2041à 2046")["year_end"] == 2046
    assert parse_label("Juillet") == {"axis": "month", "month": 7}
    assert parse_label("Réouverture du milieu en 2025 pour les lotiers") is None


def test_segment_years():
    assert segment_years(SegmentLLM(start=10, step_min=5, step_max=5), 30, "max")[0] == [10, 15, 20, 25, 30]
    ys, hyp = segment_years(SegmentLLM(start=3, step_min=2, step_max=3), 30, "max")
    assert ys[:3] == [3, 6, 9] and "2 à 3" in hyp
    assert segment_years(SegmentLLM(start=0, count_over_period=10), 30, "max")[0][0] == 0


def test_expand_gold_counts():
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    frame = Frame(**data["frame"])
    rules = [Rule(**r) for r in data["rules"]]
    for r in rules:
        r.decision = "integre"
    occs = expand(rules, frame, Settings())
    dated = [o for o in occs if o.rel_year is not None and o.span_end_rel is None]
    fauches = [o for o in dated if o.sub_action.startswith("Entretien par fauche")]
    assert len(fauches) == 60 and sum(o.optional for o in fauches) == 30
    assert {o.rel_year for o in dated if o.action_code == "ES2"} == {0, 1, 2, 3, 4, 5, 10, 15, 20, 25, 30}
    assert all(o.year == 2025 + o.rel_year for o in dated)


# ----------------------------------------------------------------- sur le PDF
@pytest.fixture(scope="module")
def gold_run(tmp_path_factory):
    from pgcal.pipeline import run
    s = Settings(offline=True, runs_dir=tmp_path_factory.mktemp("runs"))
    res, run_dir = run(Path(PDF), s, rules_json=GOLD, log=lambda *_: None)
    return res, run_dir


@needs_pdf
def test_grids_bruges(gold_run):
    _, run_dir = gold_run
    txt = (run_dir / "03_grids.txt").read_text(encoding="utf-8")
    assert "ES2 | Période d’intervention | mois colorés : avr., mai, juin" in txt   # mois codés par la couleur
    assert "RG1 | Période d’intervention | mois colorés : mars, avr., juil., août" in txt
    assert "N+6→N+10" in txt and "N = 2025" in txt
    assert "RG3" in txt   # coquille du tableau 40 lue telle quelle…


@needs_pdf
def test_gold_has_no_false_alarm(gold_run):
    res, _ = gold_run
    assert res.frame.n_year_from_grid == 2025
    assert any("RG3" in i for i in res.frame.issues)          # … puis signalée comme coquille de RG2
    alerts = [(r.rule_id, i) for r in res.rules for i in r.issues if i.startswith("[alerte]")]
    assert alerts == [], alerts
    assert all(r.decision in ("integre", "declencheur", "a_preciser") for r in res.rules)


@needs_pdf
def test_verifier_catches_errors(tmp_path):
    """Erreurs typiques d'une extraction : elles doivent faire baisser la décision."""
    from pgcal.pipeline import run
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    bad = copy.deepcopy(data)
    bad["frame"]["duration_years"], bad["frame"]["end_year"] = 5, None          # le contresens « 5 ans »
    by = {(r["action_code"], r["sub_action"][:12]): r for r in bad["rules"]}
    es1 = by[("ES1", "Suivi des ha")]
    es1["segments"] = [{"start": 0, "end": None, "step_min": 1, "step_max": 1}]  # suivi annuel au lieu de /5 ans
    es2 = by[("ES2", "Suivi de l'a")]
    es2["passes"] = [{"label": "passage", "months": [], "optional": False}]      # mois oubliés
    fg2 = by[("FG2", "Bilan interm")]
    fg2["segments"] = [{"years": [5, 10, 15, 20, 25, 30]}]                      # bilan final compté deux fois
    fg2["citations"].append({"page": 41, "quote": "un bilan intermédiaire sera produit chaque année"})  # citation inventée
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    s = Settings(offline=True, runs_dir=tmp_path / "runs")
    res, _ = run(Path(PDF), s, rules_json=p, log=lambda *_: None)

    assert any("horizon" in i for i in res.frame.issues)
    rules = {r.sub_action[:12]: r for r in res.rules}
    assert rules["Bilan interm"].decision != "integre"
    assert any("Citation introuvable" in i for i in rules["Bilan interm"].issues)
    assert rules["Suivi de l'a"].months_from_grid and rules["Suivi de l'a"].passes[0].months == [4, 5, 6]

    # même contrôle avec l'horizon juste : le suivi annuel ES1 doit être repéré par la grille
    bad["frame"]["duration_years"], bad["frame"]["end_year"] = 30, 2055
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    res, _ = run(Path(PDF), Settings(offline=True, runs_dir=tmp_path / "runs2"), rules_json=p, log=lambda *_: None)
    es1r = next(r for r in res.rules if r.action_code == "ES1")
    assert es1r.decision != "integre", es1r.issues
