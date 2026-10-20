"""Évaluation d'un calendrier produit contre un jeu de référence (gold).

    python eval/evaluate.py runs/<run>/calendar.json eval/gold/bruges_rules.json [--json out.json]

Mesures (dans cet ordre d'importance) :
- cadre : durée, année N, UG, actions (précision / rappel) ;
- règles : appariement gold <-> prédiction par action + similarité de libellé,
  puis par paire : type, années (Jaccard après déroulement identique), mois, UG ;
- occurrences datées : précision / rappel des couples (règle appariée, année).
Le déroulement des deux côtés utilise le même module (s6) et la même politique,
pour comparer des interprétations et non des conventions de comptage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pgcal.s5_verify import _sim  # noqa: E402
from pgcal.s6_expand import horizon_end, rule_years  # noqa: E402
from pgcal.schemas import CalendarResult, Frame, Rule  # noqa: E402


def _j(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def _months(r: Rule) -> set:
    return {m for p in r.passes for m in p.months}


def evaluate(pred: CalendarResult, gold_path: Path, policy: str = "max") -> dict:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    gframe = Frame(**gold["frame"])
    grules = [Rule(**r) for r in gold["rules"]]
    pframe, prules = pred.frame, pred.rules
    g_end, p_end = horizon_end(gframe), horizon_end(pframe)

    out: dict = {"frame": {
        "duration_ok": gframe.duration_years == pframe.duration_years,
        "duration": [gframe.duration_years, pframe.duration_years],
        "n_year_ok": (gframe.n_year or None) == (pframe.n_year or pframe.n_year_from_grid),
        "ugs": [sorted(u.code for u in gframe.ugs), sorted(u.code for u in pframe.ugs)],
    }}
    ga, pa = {a.code for a in gframe.actions}, {a.code for a in pframe.actions}
    out["frame"]["actions_precision"] = round(len(ga & pa) / max(1, len(pa)), 3)
    out["frame"]["actions_recall"] = round(len(ga & pa) / max(1, len(ga)), 3)

    # appariement glouton par similarité décroissante
    pairs = []
    for gi, g in enumerate(grules):
        for pi, p in enumerate(prules):
            if p.action_code != g.action_code:
                continue
            s = _sim(g.sub_action, p.sub_action) + (10 if g.kind == p.kind else 0)
            pairs.append((s, gi, pi))
    pairs.sort(reverse=True)
    used_g, used_p, matches = set(), set(), []
    for s, gi, pi in pairs:
        if s < 50 or gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        matches.append((gi, pi, s))

    details, tp_occ, n_g_occ, n_p_occ = [], 0, 0, 0
    dated = ("ponctuelle", "recurrente")
    for gi, g in enumerate(grules):
        gy = set(rule_years(g, g_end, policy)[0]) if g.kind in dated else set()
        n_g_occ += len(gy)
        m = next(((pi, s) for gj, pi, s in matches if gj == gi), None)
        if m is None:
            details.append({"gold": f"{g.action_code} · {g.sub_action}", "pred": None, "statut": "MANQUÉE"})
            continue
        p = prules[m[0]]
        py = set(rule_years(p, p_end, policy)[0]) if p.kind in dated else set()
        tp_occ += len(gy & py)
        details.append({
            "gold": f"{g.action_code} · {g.sub_action}", "pred": f"{p.rule_id} · {p.sub_action}",
            "kind_ok": g.kind == p.kind, "years_jaccard": round(_j(gy, py), 3),
            "months_jaccard": round(_j(_months(g), _months(p)), 3),
            "ug_ok": set(g.ug_codes) == set(p.ug_codes), "decision": p.decision,
            "confiance": p.confidence_detail.score,
            "manquantes": sorted(gy - py)[:12], "en_trop": sorted(py - gy)[:12],
        })
    for pi, p in enumerate(prules):
        if pi not in used_p:
            details.append({"gold": None, "pred": f"{p.rule_id} · {p.sub_action}", "statut": "EN TROP",
                            "decision": p.decision})
    for p in prules:
        if p.kind in dated:
            n_p_occ += len(rule_years(p, p_end, policy)[0])

    matched = [d for d in details if d.get("gold") and d.get("pred")]
    out["rules"] = {
        "gold": len(grules), "pred": len(prules), "matched": len(matches),
        "precision": round(len(matches) / max(1, len(prules)), 3),
        "recall": round(len(matches) / max(1, len(grules)), 3),
        "kind_accuracy": round(sum(d["kind_ok"] for d in matched) / max(1, len(matched)), 3),
        "years_jaccard_mean": round(sum(d["years_jaccard"] for d in matched) / max(1, len(matched)), 3),
        "months_jaccard_mean": round(sum(d["months_jaccard"] for d in matched) / max(1, len(matched)), 3),
        "ug_accuracy": round(sum(d["ug_ok"] for d in matched) / max(1, len(matched)), 3),
    }
    out["occurrences"] = {"gold": n_g_occ, "pred": n_p_occ, "communes": tp_occ,
                          "precision": round(tp_occ / max(1, n_p_occ), 3), "recall": round(tp_occ / max(1, n_g_occ), 3)}
    # calibration : les règles « intégrées » sont-elles vraiment justes ?
    integ = [d for d in matched if d["decision"] == "integre"]
    out["calibration"] = {
        "integrees": len(integ),
        "integrees_exactes": sum(d["years_jaccard"] == 1 and d["kind_ok"] for d in integ),
        "a_valider_ou_revoir": sum(d["decision"] in ("a_valider", "a_revoir") for d in matched),
    }
    out["details"] = details
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("calendar", type=Path)
    ap.add_argument("gold", type=Path)
    ap.add_argument("--policy", default="max")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    res = evaluate(CalendarResult(**json.loads(a.calendar.read_text(encoding="utf-8"))), a.gold, a.policy)
    f, r, o, c = res["frame"], res["rules"], res["occurrences"], res["calibration"]
    print(f"CADRE   durée {'OK' if f['duration_ok'] else 'KO'} {f['duration']} · N {'OK' if f['n_year_ok'] else 'KO'} "
          f"· actions P={f['actions_precision']} R={f['actions_recall']} · UG {f['ugs']}")
    print(f"RÈGLES  P={r['precision']} R={r['recall']} ({r['matched']}/{r['gold']} gold, {r['pred']} prédites) · "
          f"type {r['kind_accuracy']} · années {r['years_jaccard_mean']} · mois {r['months_jaccard_mean']} · UG {r['ug_accuracy']}")
    print(f"OCCUR.  P={o['precision']} R={o['recall']} ({o['communes']} communes / {o['gold']} gold / {o['pred']} prédites)")
    print(f"CALIB.  {c['integrees_exactes']}/{c['integrees']} règles intégrées exactes · {c['a_valider_ou_revoir']} à valider/revoir")
    print()
    for d in res["details"]:
        if d.get("statut"):
            print(f"  [{d['statut']}] {d['gold'] or d['pred']}")
        elif d["years_jaccard"] < 1 or not d["kind_ok"] or d["months_jaccard"] < 1 or not d["ug_ok"]:
            print(f"  [ÉCART] {d['gold']}  →  {d['pred']} · type {'ok' if d['kind_ok'] else 'KO'} · années "
                  f"{d['years_jaccard']} (manquantes {d['manquantes']}, en trop {d['en_trop']}) · mois "
                  f"{d['months_jaccard']} · UG {'ok' if d['ug_ok'] else 'KO'} · {d['decision']}")
    if a.json:
        a.json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
