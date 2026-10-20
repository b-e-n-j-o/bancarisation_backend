"""Compare une analyse d'arrêté (arrete.json) à une référence (arrete.json validé à la main).

    python eval/evaluate.py runs/<run>/arrete.json examples/bruges/arrete_reference.json

Mesures : fiche (référence, date, type, nb de VU), espèces (précision / rappel sur les noms
scientifiques), articles (découpage), obligations (appariement article + intitulé, puis exactitude
de l'échéance et de la preuve attendue), contrôles (contradictions retrouvées).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rapidfuzz import fuzz  # noqa: E402

from arx.schemas import ArreteAnalyse  # noqa: E402


def _n(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip()


def _pr(pred: set, gold: set) -> dict:
    tp = len(pred & gold)
    return {"precision": round(tp / max(1, len(pred)), 3), "recall": round(tp / max(1, len(gold)), 3),
            "manquants": sorted(gold - pred)[:10], "en_trop": sorted(pred - gold)[:10]}


def _sig(o) -> tuple:
    e = o.echeance
    return (e.kind, e.anchor, e.offset_value, e.offset_unit, e.day_month, tuple(e.years_relative), e.year_offset,
            e.window_start, e.window_end, e.date)


def evaluate(pred: ArreteAnalyse, gold: ArreteAnalyse) -> dict:
    out = {"fiche": {k: getattr(pred.fiche, k) == getattr(gold.fiche, k) for k in ("reference", "date_signature", "type_acte")}}
    out["fiche"]["visas"] = [len(pred.fiche.visas), len(gold.fiche.visas)]
    out["especes"] = _pr({_n(e.nom_scientifique) for e in pred.synthese.especes},
                         {_n(e.nom_scientifique) for e in gold.synthese.especes})
    out["articles"] = _pr({a.numero for a in pred.articles}, {a.numero for a in gold.articles})

    pairs = []
    for gi, g in enumerate(gold.obligations):
        g_arts = {g.article, *g.articles_lies}
        for pi, p in enumerate(pred.obligations):
            if not g_arts & {p.article, *p.articles_lies}:
                continue
            pairs.append((fuzz.token_set_ratio(_n(g.intitule), _n(p.intitule)), gi, pi))
    pairs.sort(reverse=True)
    ug, up, matches = set(), set(), []
    for s, gi, pi in pairs:
        if s >= 70 and gi not in ug and pi not in up:
            ug.add(gi)
            up.add(pi)
            matches.append((gi, pi))
    ech_ok = sum(_sig(gold.obligations[g]) == _sig(pred.obligations[p]) for g, p in matches)
    kind_ok = sum(gold.obligations[g].echeance.kind == pred.obligations[p].echeance.kind for g, p in matches)
    preuve_ok = sum(set(gold.obligations[g].preuve_attendue) == set(pred.obligations[p].preuve_attendue) for g, p in matches)
    out["obligations"] = {
        "gold": len(gold.obligations), "pred": len(pred.obligations), "appariees": len(matches),
        "precision": round(len(matches) / max(1, len(pred.obligations)), 3),
        "recall": round(len(matches) / max(1, len(gold.obligations)), 3),
        "echeance_exacte": round(ech_ok / max(1, len(matches)), 3),
        "type_echeance": round(kind_ok / max(1, len(matches)), 3),
        "preuve_exacte": round(preuve_ok / max(1, len(matches)), 3),
        "manquees": [f"art.{gold.obligations[i].article} {gold.obligations[i].intitule}" for i in range(len(gold.obligations)) if i not in ug][:15],
        "en_trop": [f"art.{pred.obligations[i].article} {pred.obligations[i].intitule}" for i in range(len(pred.obligations)) if i not in up][:15],
        "echeances_divergentes": [f"art.{gold.obligations[g].article} {gold.obligations[g].intitule} : {gold.obligations[g].echeance.kind} ≠ {pred.obligations[p].echeance.kind}"
                                  for g, p in matches if _sig(gold.obligations[g]) != _sig(pred.obligations[p])][:15],
    }
    g_alerts = [c for c in gold.controles if c.gravite == "alerte"]
    found = sum(any(set(c.articles) & set(p.articles) and fuzz.token_set_ratio(_n(c.sujet), _n(p.sujet)) >= 50
                    for p in pred.controles) for c in g_alerts)
    out["controles"] = {"alertes_reference": len(g_alerts), "retrouvees": found}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pred", type=Path)
    ap.add_argument("gold", type=Path)
    a = ap.parse_args()
    load = lambda p: ArreteAnalyse(**json.loads(p.read_text(encoding="utf-8")))  # noqa: E731
    r = evaluate(load(a.pred), load(a.gold))
    o = r["obligations"]
    print(f"FICHE       {r['fiche']}")
    print(f"ESPÈCES     P={r['especes']['precision']} R={r['especes']['recall']}")
    print(f"ARTICLES    P={r['articles']['precision']} R={r['articles']['recall']}")
    print(f"OBLIGATIONS P={o['precision']} R={o['recall']} ({o['appariees']}/{o['gold']}) · échéance exacte {o['echeance_exacte']} "
          f"· type d'échéance {o['type_echeance']} · preuve {o['preuve_exacte']}")
    print(f"CONTRÔLES   {r['controles']['retrouvees']}/{r['controles']['alertes_reference']} alertes de référence retrouvées")
    for k in ("manquees", "en_trop", "echeances_divergentes"):
        for x in o[k]:
            print(f"  [{k}] {x}")


if __name__ == "__main__":
    main()
