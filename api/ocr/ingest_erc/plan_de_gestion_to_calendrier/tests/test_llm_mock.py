"""Plomberie LLM testée avec un faux client (réponses tirées du gold) : formatage des prompts,
validation des schémas, consolidation des k échantillons, relecture critique, exports."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from pgcal.config import Settings
from pgcal.llm import MistralClient

ROOT = Path(__file__).resolve().parents[1]
GOLD = json.loads((ROOT / "eval" / "gold" / "bruges_rules.json").read_text(encoding="utf-8"))
PDF = os.environ.get("PGCAL_TEST_PDF")


def fake_complete(self, model, messages, schema, temperature, n, cache_key_hint):
    user = messages[-1]["content"]
    name = schema.__name__
    if name == "PageRolesLLM":
        nums = [int(x) for x in re.findall(r"\[PAGE (\d+)\]", user)]
        out = [{"number": k, "roles": ["autre"], "in_plan_de_gestion": True} for k in nums]
        return [json.dumps({"pages": out})] * n, {}
    if name == "VisionPageLLM":
        return [json.dumps({"grids": []})] * n, {}
    if name == "FrameLLM":
        f = dict(GOLD["frame"])
        f["actions"] = [a for a in f["actions"] if a["code"] != "FG2"]   # le cadre « oublie » FG2
        return [json.dumps(f, ensure_ascii=False)] * n, {}
    if name == "RulesLLM":
        code = re.search(r"action \*\*(\w+)\*\*", user).group(1)
        rules = [r for r in GOLD["rules"] if r["action_code"] == code]
        samples = [json.dumps({"rules": rules}, ensure_ascii=False)] * n
        if code == "ES1" and n >= 3:   # un échantillon sur trois se trompe de cadence
            bad = json.loads(json.dumps(rules))
            bad[0]["segments"] = [{"start": 0, "end": None, "step_min": 1, "step_max": 1}]
            samples[2] = json.dumps({"rules": bad}, ensure_ascii=False)
        return samples, {}
    if name == "CriticLLM":
        code = re.search(r"ACTION (\w+) ===", user).group(1)
        rules = [r for r in GOLD["rules"] if r["action_code"] == code]
        return [json.dumps({"rules": rules, "changes": [f"{code} : cadence alignée sur la grille"],
                            "missing_actions": []}, ensure_ascii=False)] * n, {}
    raise AssertionError(name)


@pytest.mark.skipif(not PDF or not Path(PDF).exists(), reason="PGCAL_TEST_PDF non défini")
def test_full_pipeline_with_fake_llm(tmp_path, monkeypatch):
    from pgcal.pipeline import run
    monkeypatch.setattr(MistralClient, "_complete", fake_complete)
    s = Settings(api_key="test", runs_dir=tmp_path / "runs")
    res, run_dir = run(Path(PDF), s, log=lambda *_: None)

    codes = {a.code for a in res.frame.actions}
    assert "FG2" in codes and any("FG2" in i for i in res.frame.issues)       # rattrapé par la couverture
    es1 = [r for r in res.rules if r.action_code == "ES1"]
    assert es1 and es1[0].critic_changes                                     # passé en relecture
    assert all(r.confidence_detail.score > 0 for r in res.rules)
    for f in ("02_map.json", "03_grids.txt", "04_frame.json", "04_samples.json", "05_rules.json",
              "calendar.json", "occurrences.csv", "review.md", "llm_calls.jsonl"):
        assert (run_dir / f).exists(), f
