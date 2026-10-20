"""Étape 5 — Vérification, relecture critique et score de confiance.

Contrôles déterministes sur chaque règle :
1. citations : chaque extrait cité est-il réellement dans la page indiquée ?
2. grilles : les années déroulées collent-elles aux cases cochées / bandes ?
3. mois : complétés depuis les grilles mensuelles colorées si le texte est muet ;
4. cadre : horizon du plan, UG existantes, rattachement cohérent avec le cadre.
Les actions qui posent problème repassent dans une relecture LLM (critic),
puis le score de confiance décide : intégré / à valider / à revoir.
"""
from __future__ import annotations

import json
import re
import unicodedata

from rapidfuzz import fuzz

from . import prompts
from .config import Settings
from .llm import LLMUnavailable, MistralClient
from .s3_grids import grids_as_text
from .s6_expand import horizon_end, rule_years
from .schemas import CriticLLM, DocumentText, Frame, Grid, GridRow, PassLLM, Rule

W = {"evidence": 0.30, "agreement": 0.25, "grid": 0.25, "specificity": 0.10, "llm": 0.10}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------------------- 1. citations
def check_citations(rule: Rule, doc: DocumentText, min_score: int) -> float:
    if not rule.citations:
        rule.issues.append("[alerte] Aucune citation.")
        return 0.0
    ok = 0
    for c in rule.citations:
        q = _norm(c.quote)
        if len(q) < 8:
            continue
        pages = [p for p in (c.page - 1, c.page, c.page + 1) if 1 <= p <= doc.n_pages]
        best = max((fuzz.partial_ratio(q, _norm(doc.page(p).text)) for p in pages), default=0)
        if best >= min_score:
            ok += 1
        else:
            rule.issues.append(f"[alerte] Citation introuvable p.{c.page} : « {c.quote[:80]} ».")
    return ok / len(rule.citations)


# ----------------------------------------------------------------- 2. grilles
def _col_span(g: Grid, i: int, ny: int | None) -> tuple[int, int] | None:
    c = g.columns[i]
    if c.rel_start is not None:
        return c.rel_start, c.rel_end if c.rel_end is not None else c.rel_start
    if c.year_start is not None and ny is not None:
        return c.year_start - ny, (c.year_end or c.year_start) - ny
    return None


_STOP = {"de", "du", "des", "la", "le", "les", "et", "a", "au", "aux", "en", "par", "pour", "sur", "d", "l",
         "plan", "gestion", "action", "suivi"}


def _stem(w: str) -> str:
    """Racinisation minimale du français (initiaux / initiale -> initial ; fauches -> fauch)."""
    if w.endswith("aux") and len(w) > 5:
        return w[:-3] + "al"
    for suf in ("es", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _key(s: str) -> str:
    return " ".join(_stem(w) for w in re.split(r"[^\w]+", _norm(s)) if w and w not in _STOP)


def _sim(a: str, b: str) -> int:
    a, b = _key(a), _key(b)
    if not a or not b:
        return 0
    return max(fuzz.token_set_ratio(a, b), fuzz.partial_ratio(a, b) - 10)


def _units(g: Grid, r: GridRow) -> list[dict]:
    """Une ligne de grille peut porter plusieurs sous-calendriers (ex. fiche : croix « travaux initiaux »
    en N + bande « fauche et griffage » ensuite). On compare la règle à chaque unité séparément."""
    band_cols = {i for c in r.cells if c.kind == "band" for i in c.cols}
    marks = {i for c in r.cells if c.kind in ("mark", "fill") for i in c.cols}
    units = []
    if marks:
        units.append({"kind": "marks", "labels": [r.sub_label, f"{r.sub_label} {r.action_label}"],
                      "cols": marks, "band": band_cols,
                      "bands": [(set(c.cols), c.text) for c in r.cells if c.kind == "band"]})
    for c in r.cells:
        if c.kind == "band":
            units.append({"kind": "band", "labels": [c.text], "text": c.text, "cols": set(c.cols)})
    return units


_EVERY = re.compile(r"tous les\s+(\d+)(?:\s*(?:à|a|-|ou)\s*(\d+))?\s*ans")
_ANNUAL = re.compile(r"annuel|par an|chaque ann")


def _band_period(text: str) -> tuple[int, int] | None:
    """Pas (min, max) en années annoncé dans le texte d'une bande, si lisible."""
    t = _norm(text)
    m = _EVERY.search(t)
    if m:
        lo = int(m.group(1))
        return lo, int(m.group(2) or lo)
    if _ANNUAL.search(t):
        return 1, 1
    return None


def _period_ok(rule: Rule, years: set[int], band_cols: set[int], text: str, spans: dict) -> bool:
    """Contrôle de cadence : « tous les 5 ans » sur une bande de 10 ans -> 2 occurrences attendues (±1)."""
    period = _band_period(text)
    band_spans = [spans[i] for i in band_cols if i in spans]
    if rule.kind != "recurrente" or not period or not band_spans:
        return True
    lo, hi = min(a for a, _ in band_spans), max(b for _, b in band_spans)
    n = sum(1 for y in years if lo <= y <= hi)
    length = hi - lo + 1
    return length // period[1] - 1 <= n <= -(-length // period[0]) + 1


def _consistency(rule: Rule, years: set[int], u: dict, spans: dict) -> float | None:
    if rule.kind == "continue":
        return 1.0 if u["kind"] == "band" else 0.7
    if u["kind"] == "band":   # on ne juge que l'emprise de la bande sur la période couverte par la grille
        lo, hi = min(a for a, _ in spans.values()), max(b for _, b in spans.values())
        ref = [y for y in years if lo <= y <= hi]
        if not ref:
            return None
        band_spans = [spans[i] for i in u["cols"] if i in spans]
        inside = [y for y in ref if any(a <= y <= b for a, b in band_spans)]
        score = len(inside) / len(ref)
        return score if _period_ok(rule, years, u["cols"], u["text"], spans) else score * 0.4
    evaluated = ok = 0     # croix / cellules colorées : colonne par colonne ; bandes jugées sur leur cadence
    for i, sp in spans.items():
        if i in u["band"]:
            continue
        in_rule = any(sp[0] <= y <= sp[1] for y in years)
        evaluated += 1
        ok += int(in_rule == (i in u["cols"]))
    if not evaluated:
        return None
    score = ok / evaluated
    for cols, text in u.get("bands", []):
        if not _period_ok(rule, years, cols, text, spans):
            score *= 0.4
    return score


def grid_score(rule: Rule, grids: list[Grid], ny: int | None, h_end: int, policy: str) -> float | None:
    if rule.kind in ("conditionnelle", "non_precisee"):
        return None
    years = set(rule_years(rule, h_end, policy)[0]) if rule.kind != "continue" else set()
    scores = []
    for g in grids:
        if g.axis == "month":
            continue
        cands = []
        for r in g.rows:
            if r.code != rule.action_code:
                continue
            for u in _units(g, r):
                sim = max(_sim(rule.sub_action, lab) for lab in u["labels"])
                # a priori : une règle ponctuelle ressemble à des croix, une règle récurrente à une bande
                if (rule.kind == "ponctuelle") == (u["kind"] == "marks"):
                    sim += 10
                cands.append((sim, u))
        if not cands:
            continue
        best = max(c[0] for c in cands)
        if best < 55:
            continue
        spans = {i: sp for i in range(len(g.columns)) if (sp := _col_span(g, i, ny))}
        if not spans:
            continue
        # plusieurs lectures plausibles (à 10 points près) : on garde la plus cohérente avec la règle
        scores_g = [s_ for sim, u in cands if sim >= best - 10 and (s_ := _consistency(rule, years, u, spans)) is not None]
        if scores_g:
            scores.append(max(scores_g))
    if not scores:
        return None
    sc = sum(scores) / len(scores)
    if sc < 0.8:
        rule.issues.append(f"[alerte] Années peu cohérentes avec la grille de programmation (accord {sc:.2f}).")
    return round(sc, 3)


# ----------------------------------------------------------------- 3. mois
def check_months(rule: Rule, grids: list[Grid]) -> float | None:
    rows = [r for g in grids if g.axis == "month" for r in g.rows if r.code == rule.action_code]
    if not rows or rule.kind in ("conditionnelle", "non_precisee", "continue"):
        return None
    grid_months = {g.columns[i].month for g in grids if g.axis == "month"
                   for r in g.rows if r.code == rule.action_code for c in r.cells for i in c.cols}
    grid_months.discard(None)
    rule_months = {m for p in rule.passes for m in p.months}
    if not rule_months and rule.kind == "recurrente" and grid_months:
        if rule.passes:
            for p in rule.passes:
                p.months = sorted(grid_months)
        else:
            rule.passes = [PassLLM(label="", months=sorted(grid_months))]
        rule.months_from_grid = True
        rule.issues.append("[info] Mois complétés depuis la grille mensuelle colorée de la fiche.")
        return None
    if rule_months and grid_months:
        j = len(rule_months & grid_months) / len(rule_months | grid_months)
        if j < 0.5:
            rule.issues.append(f"[alerte] Mois du texte {sorted(rule_months)} ≠ mois colorés de la fiche {sorted(grid_months)}.")
        return j
    return None


# ----------------------------------------------------------------- 4. cadre
def check_frame(rule: Rule, frame: Frame, h_end: int) -> None:
    for seg in rule.segments:
        if seg.end is not None and seg.end > h_end:
            rule.issues.append(f"[alerte] Segment jusqu'à N+{seg.end} au-delà de l'horizon du plan (N+{h_end}).")
        if seg.years and max(seg.years) > h_end:
            rule.issues.append(f"[alerte] Année N+{max(seg.years)} au-delà de l'horizon du plan (N+{h_end}).")
    if rule.anchor and rule.anchor.strip().upper() not in ("N", "N=0", "ANNEE N"):
        rule.issues.append(f"[alerte] Années relatives à « {rule.anchor} » et non à N : à recaler sur le calendrier.")
    ug_codes = {u.code for u in frame.ugs}
    unknown = [u for u in rule.ug_codes if ug_codes and u not in ug_codes]
    if unknown:
        rule.issues.append(f"[alerte] UG inconnues du cadre : {unknown}.")
    action = next((a for a in frame.actions if a.code == rule.action_code), None)
    if action and action.ug_codes and rule.ug_codes and set(action.ug_codes) != set(rule.ug_codes):
        rule.issues.append(f"[info] UG de la règle {rule.ug_codes} ≠ UG de l'action dans le cadre {action.ug_codes}.")


def frame_checks(frame: Frame, grids: list[Grid]) -> None:
    if not frame.duration_years and not frame.end_year:
        frame.issues.append("[alerte] Durée du plan non trouvée : horizon par défaut N+30.")
    h_end = horizon_end(frame)
    g_max = max((c.rel_end for g in grids for c in g.columns if c.rel_end is not None), default=None)
    if g_max is not None and g_max != h_end:
        frame.issues.append(f"[alerte] Les grilles de programmation vont jusqu'à N+{g_max} mais la durée extraite "
                            f"donne un horizon N+{h_end} : vérifier la durée du plan.")
    if frame.n_year and frame.n_year_from_grid and frame.n_year != frame.n_year_from_grid:
        frame.issues.append(f"[alerte] Année N du texte ({frame.n_year}) ≠ année N des grilles ({frame.n_year_from_grid}).")
    if not frame.n_year and not frame.n_year_from_grid:
        frame.issues.append("[info] Année calendaire de N inconnue : calendrier en années relatives uniquement.")


# ----------------------------------------------------------------- score
def _specificity(rule: Rule) -> float:
    s = 1.0
    if any(seg.step_max > seg.step_min for seg in rule.segments):
        s -= 0.3
    if any(seg.count_over_period for seg in rule.segments):
        s -= 0.3
    if rule.months_from_grid:
        s -= 0.05
    s -= min(0.3, 0.1 * len(rule.ambiguities))
    return max(0.0, s)


def score(rule: Rule, settings: Settings) -> None:
    d = rule.confidence_detail
    d.specificity = _specificity(rule)
    d.llm = rule.confidence
    parts = {"evidence": d.evidence, "agreement": d.agreement, "specificity": d.specificity, "llm": d.llm}
    weights = dict(W)
    if d.grid is None:
        weights.pop("grid")
    else:
        parts["grid"] = d.grid
    total = sum(weights.values())
    d.score = round(sum(parts[k] * w for k, w in weights.items()) / total, 3)
    if rule.kind == "conditionnelle":
        rule.decision = "declencheur"
    elif rule.kind == "non_precisee":
        rule.decision = "a_preciser"
    elif d.score >= settings.auto_threshold and not any(i.startswith("[alerte]") for i in rule.issues):
        rule.decision = "integre"
    elif d.score >= settings.review_threshold:
        rule.decision = "a_valider"
    else:
        rule.decision = "a_revoir"


def _run_checks(rules: list[Rule], frame: Frame, grids: list[Grid], doc: DocumentText, settings: Settings) -> None:
    h_end = horizon_end(frame)
    horizon_doubt = any("horizon" in i and i.startswith("[alerte]") for i in frame.issues)
    ny = settings.n_year_override or frame.n_year or frame.n_year_from_grid
    for r in rules:
        r.issues = [i for i in r.issues if "échantillons" in i]   # on garde l'info d'auto-cohérence
        r.confidence_detail.evidence = round(check_citations(r, doc, settings.citation_min_score), 3)
        m = check_months(r, grids)
        g = grid_score(r, grids, ny, h_end, settings.step_policy)
        if g is not None and m is not None:
            g = round(0.75 * g + 0.25 * m, 3)
        r.confidence_detail.grid = g if g is not None else m
        check_frame(r, frame, h_end)
        if horizon_doubt and r.kind in ("recurrente", "continue") and any(sg.end is None for sg in r.segments):
            r.issues.append(f"[alerte] Série « jusqu'à la fin du plan » : horizon N+{h_end} douteux (voir cadre).")
        if r.ambiguities:
            r.issues += [f"[info] Ambiguïté signalée : {a}" for a in r.ambiguities]


# ----------------------------------------------------------------- critic
def _needs_critic(rules: list[Rule]) -> bool:
    return any(any(i.startswith("[alerte]") for i in r.issues) or r.confidence_detail.agreement < 1
               or (r.confidence_detail.grid is not None and r.confidence_detail.grid < 0.8) for r in rules)


def critic(code: str, rules: list[Rule], frame: Frame, doc_txt: str, grids: list[Grid],
           llm: MistralClient, doc_sha: str) -> tuple[list[Rule], list[str]]:
    issues = [f"{r.rule_id} ({r.sub_action}) : {i}" for r in rules for i in r.issues if i.startswith("[alerte]")]
    payload = json.dumps([r.model_dump(exclude={"confidence_detail", "decision", "issues", "critic_changes",
                                                "months_from_grid", "support"}) for r in rules], ensure_ascii=False)
    frame_json = json.dumps(frame.model_dump(include={"duration_years", "n_definition", "n_year", "ugs", "actions"}),
                            ensure_ascii=False)
    res = llm.chat_json(CriticLLM, prompts.SYSTEM_BASE,
                        prompts.CRITIC.format(document=doc_txt, grids=grids_as_text(grids), frame=frame_json,
                                              code=code, rules=payload, issues="\n".join(issues) or "(aucun)"),
                        tag=f"critic_{code}", temperature=0, cache_key_hint=doc_sha)
    if not res:
        return rules, []
    out = []
    for i, nr in enumerate(res[0].rules, 1):
        prev = max(rules, key=lambda r: fuzz.token_set_ratio(r.sub_action, nr.sub_action), default=None)
        same = prev is not None and fuzz.token_set_ratio(prev.sub_action, nr.sub_action) >= 70
        r = Rule(**nr.model_dump(), rule_id=f"{code}-{i:02d}", support=prev.support if same else 0.5)
        r.action_code = code
        r.confidence_detail.agreement = prev.confidence_detail.agreement if same else 0.5
        r.critic_changes = list(res[0].changes)
        out.append(r)
    missing = [f"[alerte] Relecture {code} : action possiblement manquante {m}." for m in res[0].missing_actions]
    return out, missing


# ----------------------------------------------------------------- étape
def verify(rules: list[Rule], frame: Frame, grids: list[Grid], doc: DocumentText, doc_txt: str,
           settings: Settings, llm: MistralClient) -> tuple[list[Rule], list[str]]:
    issues: list[str] = []
    frame_checks(frame, grids)
    _run_checks(rules, frame, grids, doc, settings)
    if settings.use_critic:
        by_code: dict[str, list[Rule]] = {}
        for r in rules:
            by_code.setdefault(r.action_code, []).append(r)
        new_rules: list[Rule] = []
        for code, rs in by_code.items():
            if not _needs_critic(rs):
                new_rules += rs
                continue
            try:
                fixed, miss = critic(code, rs, frame, doc_txt, grids, llm, doc.sha256)
                issues += miss
                _run_checks(fixed, frame, grids, doc, settings)
                new_rules += fixed
            except LLMUnavailable as e:
                issues.append(f"Relecture critique non réalisée ({e}).")
                new_rules += rs
                settings.use_critic = False
        rules = new_rules
    for r in rules:
        score(r, settings)
    return rules, issues
