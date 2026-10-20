"""Étape 6 — Déroulement : règles -> occurrences datées. 100 % déterministe.

Le LLM ne compte jamais : il décrit des règles (segments, pas, passages),
ce module les déroule sur l'horizon du plan avec des hypothèses explicites
quand le document donne une fourchette (« tous les 2 à 3 ans »).
"""
from __future__ import annotations

from .config import Settings
from .schemas import Frame, Occurrence, PassLLM, Rule, SegmentLLM

DECISION_ORDER = ["integre", "a_valider", "a_revoir"]


def horizon_end(frame: Frame) -> int:
    """Dernière année relative couverte par le plan."""
    n = frame.n_year or frame.n_year_from_grid
    if frame.end_year and n:
        return frame.end_year - n
    if frame.duration_years:
        return frame.duration_years
    return 30   # défaut prudent, signalé par s5 si le cadre n'a pas de durée


def n_year(frame: Frame, settings: Settings) -> int | None:
    return settings.n_year_override or frame.n_year or frame.n_year_from_grid


def _step(seg: SegmentLLM, policy: str) -> int:
    lo, hi = max(1, seg.step_min), max(1, seg.step_max, seg.step_min)
    if policy == "min":
        return lo
    if policy == "mid":
        return round((lo + hi) / 2)
    return hi


def segment_years(seg: SegmentLLM, h_end: int, policy: str) -> tuple[list[int], str]:
    """Années relatives d'un segment + hypothèse éventuelle (texte vide si aucune)."""
    if seg.years:
        return sorted({y for y in seg.years if 0 <= y <= h_end}), ""
    start = 0 if seg.start is None else seg.start
    end = h_end if seg.end is None else min(seg.end, h_end)
    if end < start:
        return [], ""
    if seg.count_over_period:
        n = seg.count_over_period
        span = end - start
        ys = sorted({start + round(i * span / max(1, n - 1)) for i in range(n)}) if n > 1 else [start]
        return ys, f"{n} interventions réparties régulièrement entre N+{start} et N+{end}"
    step = _step(seg, policy)
    hyp = ""
    if seg.step_max > seg.step_min:
        hyp = f"pas de {step} ans retenu (document : tous les {seg.step_min} à {seg.step_max} ans ; politique « {policy} »)"
    return list(range(start, end + 1, step)), hyp


def rule_years(rule: Rule | object, h_end: int, policy: str) -> tuple[list[int], list[str]]:
    years, hyps = set(), []
    for seg in getattr(rule, "segments", []):
        ys, h = segment_years(seg, h_end, policy)
        years.update(ys)
        if h:
            hyps.append(h)
    return sorted(years), hyps


def _cap(decision: str, cap: str) -> str:
    if decision not in DECISION_ORDER:
        return decision
    return DECISION_ORDER[max(DECISION_ORDER.index(decision), DECISION_ORDER.index(cap))]


def expand(rules: list[Rule], frame: Frame, settings: Settings) -> list[Occurrence]:
    h_end = horizon_end(frame)
    ny = n_year(frame, settings)
    occs: list[Occurrence] = []

    def add(rule: Rule, rel, **kw):
        occs.append(Occurrence(
            occ_id=f"OCC-{len(occs) + 1:04d}", rule_id=rule.rule_id, action_code=rule.action_code,
            sub_action=rule.sub_action, ug_codes=rule.ug_codes, rel_year=rel,
            year=(ny + rel) if (ny is not None and rel is not None) else None,
            source_pages=sorted({c.page for c in rule.citations}), **kw))

    for r in rules:
        score = r.confidence_detail.score
        if r.kind == "conditionnelle":
            add(r, None, decision="declencheur", confidence=score, hypothesis=f"déclencheur : {r.trigger}".strip())
            continue
        if r.kind == "non_precisee":
            add(r, None, decision="a_preciser", confidence=score,
                hypothesis=f"récurrence sans fréquence exploitable : {r.frequency_text}".strip())
            continue
        if r.kind == "continue":
            seg = r.segments[0] if r.segments else SegmentLLM()
            start = seg.start or (min(seg.years) if seg.years else 0)
            end = h_end if seg.end is None else min(seg.end, h_end)
            add(r, start, span_end_rel=end, decision=r.decision, confidence=score,
                pass_label="activité continue")
            continue

        years, hyps = rule_years(r, h_end, settings.step_policy)
        if r.anchor and r.anchor.strip().upper() not in ("N", "N=0", "ANNEE N"):
            hyps.append(f"années comptées depuis « {r.anchor} », recalées sur N par défaut")
        passes = r.passes or []
        if not passes:
            passes = [PassLLM(label="", months=[], optional=False)]
        for y in years:
            for p in passes:
                hyp = "; ".join(hyps)
                decision, conf = r.decision, score
                if p.optional:
                    decision, conf = _cap(decision, "a_valider"), conf - 0.1
                if hyp:
                    decision, conf = _cap(decision, "a_valider"), conf - 0.1
                add(r, y, months=p.months, pass_label=p.label, optional=p.optional, hypothesis=hyp,
                    decision=decision, confidence=round(max(0.0, conf), 3))
    return occs
