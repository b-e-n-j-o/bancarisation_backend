"""Étape 2 — Repérage : quelles pages relèvent du plan de gestion, quel rôle
a chaque page (cadre, fiche, programmation…), quels codes d'action existent.

Deux couches complémentaires :
- heuristiques (regex) : codes d'action / d'UG, en-têtes de grille, indices de
  durée et d'année N — rapides, sans coût, et servent de garde-fou ;
- classification LLM par lots de pages : rôles, code de la fiche (y compris
  les pages de suite qui ne répètent pas le titre), appartenance au plan.
"""
from __future__ import annotations

import re
from collections import Counter

from . import prompts
from .config import Settings
from .llm import LLMUnavailable, MistralClient
from .schemas import DocumentMap, DocumentText, Evidence, PageInfo, PageRolesLLM

CODE_RE = re.compile(r"\b([A-Z]{1,4})\s?-?(\d{1,2}(?:\.\d{1,2})?)\b")
UG_RE = re.compile(r"\b(UG|UGE|US)\s?-?(\d{1,2}[a-z]?)\b|\b(Secteur|Unité|Zone)\s+([A-Z0-9]{1,3})\b")
NOT_ACTION_PREFIX = {"UG", "UGE", "US", "N", "SNC", "RD", "RN", "AT", "EUR", "HT", "TTC", "CO", "PM", "PDF", "ZH", "PLU"}
FICHE_TITLE_RE = re.compile(r"^\s*([A-Z]{1,4}\s?-?\d{1,2}(?:\.\d{1,2})?)\s*[:\-–—]?\s+[A-ZÉÈÀ][\wÉÈÀéèêàçô’' ]{4,}")
FICHE_LABELS = ("Intervenant", "Phasage", "Indicateur", "Période d", "Localisation", "Objectif", "Description de l")
REL_HDR = re.compile(r"\bN\s*\+\s*\d{1,2}\b")
YEAR_HDR = re.compile(r"\b(19|20)\d{2}\b")
MONTHS = ("janvier", "février", "fevrier", "mars", "avril", "mai", "juin", "juillet", "août", "aout",
          "septembre", "octobre", "novembre", "décembre", "decembre")
HORIZON_RE = re.compile(
    r"([^.\n]{0,90}\b(\d{1,2})\s*ans\b[^.\n]{0,90})|([^.\n]{0,60}\bN\s*\+\s*(\d{2})\b[^.\n]{0,40})|"
    r"([^.\n]{0,60}\b(20\d{2})\s*[-–à]\s*(20\d{2})\b[^.\n]{0,40})")
HORIZON_KW = ("gestion", "engagement", "durée", "duree", "période", "periode", "plan", "compensation", "minimale")
NYEAR_RE = re.compile(r"\bN\s*[=:(]\s*(20\d{2})|\bN\b\s{1,40}N\s*\+\s*1\b")


def _compact(text: str) -> str:
    return re.sub(r"[ \t]{3,}", "   ", text)


def _norm_code(prefix: str, num: str) -> str:
    return f"{prefix}{num}"


def _heuristics(doc: DocumentText) -> tuple[list[PageInfo], Counter, Counter, list[Evidence], list[Evidence]]:
    infos, codes, ugs, horizon, nyear = [], Counter(), Counter(), [], []
    current_fiche = None
    for p in doc.pages:
        t = p.text
        page_codes = []
        for m in CODE_RE.finditer(t):
            pre, num = m.group(1), m.group(2)
            if pre in NOT_ACTION_PREFIX:
                continue
            c = _norm_code(pre, num)
            codes[c] += 1
            page_codes.append(c)
        for m in UG_RE.finditer(t):
            ugs[(m.group(1) or m.group(3)) + (m.group(2) or " " + (m.group(4) or ""))] += 1

        # fiche : titre en tête de page, sinon suite de la fiche précédente
        head = [l for l in t.splitlines() if l.strip()][:10]
        title = next((FICHE_TITLE_RE.match(l) for l in head if FICHE_TITLE_RE.match(l)), None)
        if title:
            current_fiche = re.sub(r"[\s-]", "", title.group(1))
        elif not any(lbl in t for lbl in FICHE_LABELS):
            current_fiche = None
        fiche = current_fiche if any(lbl in t for lbl in FICHE_LABELS) else None

        low = t.lower()
        grid = (len(REL_HDR.findall(t)) >= 4 or sum(low.count(m) > 0 for m in MONTHS) >= 8
                or any(len(YEAR_HDR.findall(l)) >= 6 for l in t.splitlines()))
        infos.append(PageInfo(number=p.number, fiche_code=fiche, codes=sorted(set(page_codes)), grid_hint=grid))

        for m in HORIZON_RE.finditer(t):
            s = re.sub(r"\s+", " ", m.group(0)).strip()
            if any(k in s.lower() for k in HORIZON_KW) or m.group(4) or m.group(6):
                horizon.append(Evidence(page=p.number, text=s[:220]))
        for m in NYEAR_RE.finditer(t):
            nyear.append(Evidence(page=p.number, text=re.sub(r"\s+", " ", t[max(0, m.start() - 40): m.end() + 120])[:220]))
    return infos, codes, ugs, horizon[:40], nyear[:10]


def _llm_roles(doc: DocumentText, infos: list[PageInfo], settings: Settings, llm: MistralClient) -> None:
    b = settings.page_classify_batch
    for start in range(0, doc.n_pages, b):
        chunk = doc.pages[start:start + b]
        pages_txt = "\n\n".join(f"[PAGE {p.number}]\n" + _compact(p.text)[:1400] for p in chunk)
        hints = "\n".join(
            f"p{i.number}: codes={i.codes[:8]} grille={'oui' if i.grid_hint else 'non'} fiche_regex={i.fiche_code}"
            for i in infos[start:start + b])
        res = llm.chat_json(PageRolesLLM, prompts.SYSTEM_BASE, prompts.PAGE_ROLES.format(hints=hints, pages=pages_txt),
                            tag=f"roles_{start + 1}", temperature=0)
        if not res:
            continue
        for r in res[0].pages:
            if 1 <= r.number <= doc.n_pages:
                info = infos[r.number - 1]
                info.roles = list(r.roles)
                info.in_plan_de_gestion = r.in_plan_de_gestion
                if r.fiche_code:
                    info.fiche_code = re.sub(r"[\s-]", "", r.fiche_code)


def _heuristic_roles(infos: list[PageInfo], doc: DocumentText) -> None:
    for info, p in zip(infos, doc.pages):
        t = p.text.lower()
        roles = []
        if info.fiche_code:
            roles.append("fiche_action")
        if info.grid_hint:
            roles.append("programmation")
        if "unité de gestion" in t or "unités de gestion" in t:
            roles.append("unites_gestion")
        if "coût" in t or "€" in t:
            roles.append("couts")
        if "plan de gestion" in t and ("ans" in t or "période" in t):
            roles.append("cadre")
        info.roles = roles or ["autre"]


def locate(doc: DocumentText, settings: Settings, llm: MistralClient) -> tuple[DocumentMap, list[str]]:
    issues: list[str] = []
    infos, codes, ugs, horizon, nyear = _heuristics(doc)
    try:
        _llm_roles(doc, infos, settings, llm)
    except LLMUnavailable as e:
        issues.append(f"Classification des pages par règles simples uniquement ({e}).")
        _heuristic_roles(infos, doc)

    pg_pages = [i.number for i in infos if i.in_plan_de_gestion]
    if not pg_pages:
        pg_pages = [i.number for i in infos]
        issues.append("Aucune page identifiée comme plan de gestion : tout le document est utilisé.")

    # codes d'action plausibles : vus >= 2 fois, sur des pages du plan
    pg_set = set(pg_pages)
    code_pages = Counter(c for i in infos if i.number in pg_set for c in i.codes)
    code_candidates = {c: n for c, n in codes.most_common() if n >= 2 and code_pages[c] >= 1}
    dm = DocumentMap(pages=infos, pg_pages=pg_pages, code_candidates=code_candidates,
                     ug_candidates=dict(ugs.most_common(20)), horizon_evidence=horizon, n_year_evidence=nyear)
    return dm, issues
