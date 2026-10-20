"""Étape 4 — Extraction LLM : cadre du plan, puis règles temporelles par action.

- Le modèle reçoit le texte COMPLET du plan (1M tokens de contexte : pas de
  découpage tant que le plan tient) + la transcription des grilles (étape 3).
  C'est ce qui évite les contresens du type « 5 ans » lu hors contexte.
- Une requête par action, `samples_k` échantillons indépendants : l'accord
  entre échantillons alimente le score de confiance.
- Le préfixe (document + grilles) est identique pour toutes les actions :
  `prompt_cache_key` permet à l'API de le mettre en cache.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from rapidfuzz import fuzz

from . import prompts
from .config import Settings
from .llm import MistralClient
from .s1_ingest import document_as_prompt, estimate_tokens
from .s3_grids import grids_as_text
from .s6_expand import horizon_end, rule_years
from .schemas import (ActionRefLLM, DocumentMap, DocumentText, Frame, FrameLLM, Grid, Rule, RuleLLM,
                      RulesLLM)


# ----------------------------------------------------------------- contexte
def build_context(doc: DocumentText, dm: DocumentMap, grids: list[Grid], settings: Settings) -> tuple[str, str]:
    numbers = dm.pg_pages
    text = document_as_prompt(doc, numbers)
    if estimate_tokens(text) > settings.full_context_max_tokens:
        useful = [i.number for i in dm.pages if i.number in set(numbers) and set(i.roles) - {"autre", "hors_plan"}]
        text = document_as_prompt(doc, useful)
    return text, grids_as_text(grids)


# ----------------------------------------------------------------- cadre
def _code(c: str) -> str:
    return re.sub(r"[\s\-–]", "", c or "").upper()


def extract_frame(doc_txt: str, grids_txt: str, grids: list[Grid], dm: DocumentMap,
                  llm: MistralClient, doc_sha: str) -> Frame:
    res = llm.chat_json(FrameLLM, prompts.SYSTEM_BASE, prompts.FRAME.format(document=doc_txt, grids=grids_txt),
                        tag="frame", temperature=0, cache_key_hint=doc_sha)
    frame = Frame(**(res[0].model_dump() if res else {}))
    for a in frame.actions:
        a.code = _code(a.code)
    votes = Counter(g.n_year for g in grids if g.n_year is not None)
    frame.n_year_from_grid = votes.most_common(1)[0][0] if votes else None
    complete_catalog(frame, grids, dm)
    return frame


def complete_catalog(frame: Frame, grids: list[Grid], dm: DocumentMap) -> None:
    """Garde-fou de couverture : codes vus en fiche ou en grille mais absents du cadre."""
    known = {a.code: a for a in frame.actions}
    fiche_codes = {_code(i.fiche_code) for i in dm.pages if i.fiche_code}
    grid_rows = [(_code(r.code), r) for g in grids for r in g.rows if r.code]
    for code in sorted(fiche_codes | {c for c, _ in grid_rows}):
        if code in known:
            continue
        label = next((r.action_label or r.sub_label for c, r in grid_rows if c == code), "")
        # coquille probable ? même intitulé qu'une action connue
        twin = max(known.values(), key=lambda a: fuzz.token_set_ratio(a.title, label), default=None)
        if twin and label and fuzz.token_set_ratio(twin.title, label) >= 85:
            frame.issues.append(f"[alerte] Code {code} (grille) a le même intitulé que {twin.code} : coquille probable, "
                                f"traité comme {twin.code}.")
            for g in grids:
                for r in g.rows:
                    if _code(r.code) == code:
                        r.code = twin.code
            continue
        if code in fiche_codes or sum(1 for c, _ in grid_rows if c == code) >= 1:
            frame.actions.append(ActionRefLLM(code=code, title=label or code))
            known[code] = frame.actions[-1]
            frame.issues.append(f"[alerte] Action {code} vue dans les fiches/grilles mais absente du cadre extrait : ajoutée.")


# ----------------------------------------------------------------- règles
def _signature(rule: RuleLLM, h_end: int, policy: str) -> tuple:
    years, _ = rule_years(rule, h_end, policy)
    months = tuple(sorted({m for p in rule.passes for m in p.months}))
    return (rule.kind, tuple(years), months, len(rule.passes))


def consolidate(samples: list[list[RuleLLM]], code: str, h_end: int, policy: str) -> list[Rule]:
    """Regroupe les règles équivalentes des k échantillons ; support et accord par groupe."""
    k = max(1, len(samples))
    clusters: list[list[tuple[int, RuleLLM]]] = []
    for si, rules in enumerate(samples):
        for r in rules:
            best, best_s = None, 0
            for cl in clusters:
                rep = cl[0][1]
                s = fuzz.token_set_ratio(rep.sub_action.lower(), r.sub_action.lower())
                if set(rep.ug_codes) != set(r.ug_codes) and rep.ug_codes and r.ug_codes:
                    s -= 15
                if s > best_s and all(sj != si for sj, _ in cl):
                    best, best_s = cl, s
            if best is not None and best_s >= 70:
                best.append((si, r))
            else:
                clusters.append([(si, r)])
    out: list[Rule] = []
    for cl in sorted(clusters, key=lambda c: -len(c)):
        sigs = Counter(_signature(r, h_end, policy) for _, r in cl)
        modal, n_modal = sigs.most_common(1)[0]
        rep = max((r for _, r in cl if _signature(r, h_end, policy) == modal), key=lambda r: r.confidence)
        support = len({si for si, _ in cl}) / k
        rule = Rule(**rep.model_dump(), support=support)
        rule.action_code = code
        rule.confidence_detail.agreement = round(support * n_modal / len(cl), 3)
        if support < 1:
            rule.issues.append(f"[alerte] Règle présente dans {len({si for si, _ in cl})}/{k} échantillons.")
        if n_modal < len(cl):
            rule.issues.append(f"[alerte] Les échantillons divergent sur les années/mois ({n_modal}/{len(cl)} identiques).")
        out.append(rule)
    for i, r in enumerate(out, 1):
        r.rule_id = f"{code}-{i:02d}"
    return out


def extract_rules(frame: Frame, doc_txt: str, grids_txt: str, settings: Settings,
                  llm: MistralClient, doc_sha: str) -> tuple[list[Rule], dict]:
    frame_json = json.dumps(frame.model_dump(include={"duration_years", "n_definition", "n_year", "start_year",
                                                      "end_year", "ugs", "actions"}), ensure_ascii=False)
    n_year = frame.n_year or frame.n_year_from_grid
    h_end = horizon_end(frame)
    all_rules: list[Rule] = []
    raw: dict = {}
    for a in frame.actions:
        prompt = prompts.RULES.format(document=doc_txt, grids=grids_txt, frame=frame_json,
                                      code=a.code, title=a.title, n_year=n_year if n_year else "inconnue")
        res = llm.chat_json(RulesLLM, prompts.SYSTEM_BASE, prompt, tag=f"rules_{a.code}",
                            n=settings.samples_k, cache_key_hint=doc_sha)
        samples = [r.rules for r in res]
        raw[a.code] = [[r.model_dump() for r in s] for s in samples]
        if not samples:
            frame.issues.append(f"[alerte] Aucune règle extraite pour {a.code}.")
            continue
        all_rules += consolidate(samples, a.code, h_end, settings.step_policy)
    return all_rules, raw
