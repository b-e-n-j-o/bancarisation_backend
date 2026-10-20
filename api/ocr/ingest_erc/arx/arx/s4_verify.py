"""Étape 4 — Vérification : citations, doublons entre articles, contradictions internes,
couverture, puis score de confiance et décision par obligation.

Contradictions, deux détecteurs complémentaires :
- par règle : obligations au même objet (intitulé proche ou même preuve attendue) énoncées
  dans des articles différents avec des échéances différentes -> alerte ; mêmes objets
  quantifiés avec des valeurs différentes -> info ;
- par LLM : relecture de l'acte entier avec la liste des obligations (renvois erronés,
  chiffres incohérents, délais impossibles…). Ses citations sont vérifiées comme les autres.
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from . import prompts
from .config import Settings
from .llm import LLMUnavailable, MistralClient
from .s1_ingest import DocumentText, as_prompt
from .s2_segment import Segmented
from .s3_extract import structure_hint
from .schemas import (Article, Citation, Controle, ControlesLLM, FicheLLM, Obligation, Synthese)

W = {"evidence": 0.35, "agreement": 0.30, "specificity": 0.20, "llm": 0.15}
MONTH = r"(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)"
DEADLINE_RE = re.compile(
    r"\b(dans un délai d[e']|au plus tard|avant le \d|à compter de|sans délai|jusqu['’]au \d|à l['’]issue de"
    r"|entre (?:le )?(?:\d|1er|début|mi-|fin)|chaque année|tous les \d+ ans|N\s*\+\s*\d+|" + MONTH + r" à " + MONTH + r")", re.I)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("«", '"').replace("»", '"')).strip()


# ----------------------------------------------------------------- citations
def verify_citations(cits: list[Citation], doc: DocumentText, min_score: int) -> float:
    if not cits:
        return 0.0
    ok = 0
    for c in cits:
        q = _norm(c.quote)
        pages = [p for p in (c.page - 1, c.page, c.page + 1) if 1 <= p <= doc.n_pages]
        best = max((fuzz.partial_ratio(q, _norm(doc.page(p).text)) for p in pages), default=0) if len(q) >= 8 else 0
        c.verified = best >= min_score
        ok += c.verified
    return ok / len(cits)


# ----------------------------------------------------------------- doublons et contradictions par règle
def _ech_sig(o: Obligation) -> tuple:
    e = o.echeance
    return (e.kind, e.date, e.anchor, e.offset_value, e.offset_unit, e.day_month, tuple(e.years_relative),
            e.year_offset, e.window_start, e.window_end)


def _ech_txt(o: Obligation) -> str:
    return o.echeance.texte or str({k: v for k, v in o.echeance.model_dump().items() if v not in (None, [], "")})


def _same_subject(a: Obligation, b: Obligation) -> bool:
    s = fuzz.token_set_ratio(_norm(a.intitule), _norm(b.intitule))
    if s >= 85:
        return True
    shared = set(a.preuve_attendue) & set(b.preuve_attendue) - {"autre", "aucune", "courrier"}
    return bool(shared) and a.categorie == b.categorie and s >= 65


def dedupe_and_contradictions(obls: list[Obligation]) -> tuple[list[Obligation], list[Controle]]:
    controls: list[Controle] = []
    kept: list[Obligation] = []
    for o in obls:
        twin = next((k for k in kept if k.article != o.article and _same_subject(k, o)), None)
        if twin is None:
            kept.append(o)
            continue
        vague = ("non_precisee", "permanente")
        if _ech_sig(twin) == _ech_sig(o) or o.echeance.kind in vague or twin.echeance.kind in vague:
            # même obligation redite ailleurs (ou précisée ailleurs) : fusion, on garde l'échéance la plus précise
            twin.articles_lies = sorted(set(twin.articles_lies + [o.article]))
            twin.citations += [c for c in o.citations if c.quote not in {x.quote for x in twin.citations}]
            twin.support = max(twin.support, o.support)
            if twin.echeance.kind in vague and o.echeance.kind not in vague:
                twin.echeance = o.echeance
            continue
        kept.append(o)
        msg = (f"« {twin.intitule} » (art. {twin.article}) : échéance « {_ech_txt(twin)} » ; "
               f"« {o.intitule} » (art. {o.article}) : échéance « {_ech_txt(o)} ».")
        controls.append(Controle(sujet=f"Échéances divergentes : {twin.intitule}", description=msg, gravite="alerte",
                                 articles=[twin.article, o.article], valeurs=[_ech_txt(twin), _ech_txt(o)],
                                 citations=(twin.citations[:1] + o.citations[:1]), origine="contradiction_regle"))
        for x in (twin, o):
            x.issues.append(f"[alerte] Échéance en contradiction avec l'article {o.article if x is twin else twin.article}.")
    # quantités : même objet, même unité, valeurs différentes
    seen: dict[tuple, tuple[float, str]] = {}
    for o in kept:
        for q in o.quantites:
            if q.valeur is None or not q.objet:
                continue
            k = (_norm(q.objet)[:40], q.unite)
            if k in seen and seen[k][0] != q.valeur and seen[k][1] != o.article:
                controls.append(Controle(sujet=f"Quantité divergente : {q.objet}", gravite="info",
                                         description=f"{seen[k][0]} {q.unite} (art. {seen[k][1]}) contre {q.valeur} {q.unite} (art. {o.article}).",
                                         articles=[seen[k][1], o.article], origine="contradiction_regle"))
            seen.setdefault(k, (q.valeur, o.article))
    return kept, controls


# ----------------------------------------------------------------- contradictions par LLM
def llm_controls(doc: DocumentText, seg: Segmented, obls: list[Obligation], llm: MistralClient,
                 settings: Settings) -> list[Controle]:
    summary = "\n".join(f"- {o.id} art.{o.article}{'+' + ','.join(o.articles_lies) if o.articles_lies else ''} "
                        f"[{o.categorie}] {o.intitule} — échéance : {_ech_txt(o)}" for o in obls)
    res = llm.chat_json(ControlesLLM, prompts.SYSTEM,
                        prompts.CONTROLES.format(document=as_prompt(doc), structure=structure_hint(seg), obligations=summary),
                        tag="controles", temperature=0, cache_key_hint=doc.sha256)
    out = []
    for c in (res[0].controles if res else []):
        ctl = Controle(**c.model_dump(), origine="contradiction_llm")
        ev = verify_citations(ctl.citations, doc, settings.citation_min_score)
        if ctl.citations and ev == 0:
            ctl.gravite = "info"
            ctl.description += " (citations non retrouvées dans le texte : à vérifier)"
        out.append(ctl)
    return out


# ----------------------------------------------------------------- couverture
def coverage(articles: list[Article], obls: list[Obligation]) -> list[Controle]:
    out = []
    by_art: dict[str, list[Obligation]] = {}
    for o in obls:
        for a in [o.article] + o.articles_lies:
            by_art.setdefault(a, []).append(o)
    for a in articles:
        if not a.texte.strip() or set(a.categories) & {"recours", "execution", "objet"}:
            continue
        n_deadlines = len(DEADLINE_RE.findall(a.texte))
        timed = [o for o in by_art.get(a.numero, []) if o.echeance.kind not in ("non_precisee", "permanente")]
        if n_deadlines >= 2 and len(timed) == 0:
            out.append(Controle(sujet=f"Échéances possiblement manquées (art. {a.numero})", gravite="info",
                                description=f"{n_deadlines} formulations d'échéance dans le texte, aucune obligation datée extraite.",
                                articles=[a.numero], origine="couverture"))
        prescriptive = {"evitement", "reduction", "compensation", "accompagnement", "suivi_ecologique",
                        "transmission_documents", "suivi_chantier", "calendrier_travaux"}
        if set(a.categories) & prescriptive and not by_art.get(a.numero):
            out.append(Controle(sujet=f"Article prescriptif sans obligation (art. {a.numero})", gravite="info",
                                description="Article classé prescriptif mais aucune obligation extraite.",
                                articles=[a.numero], origine="couverture"))
    return out


# ----------------------------------------------------------------- confiance
def score(o: Obligation, settings: Settings) -> None:
    c = o.confiance
    spec = 1.0
    if o.echeance.kind == "non_precisee":
        spec -= 0.4
    if o.echeance.anchor in ("autre",) and not o.echeance.anchor_label:
        spec -= 0.2
    if o.echeance.kind in ("delai_apres", "delai_avant") and (o.echeance.offset_value is None or not o.echeance.anchor):
        spec -= 0.3
    spec -= min(0.3, 0.1 * len(o.ambiguities))
    c.specificity = max(0.0, round(spec, 3))
    c.agreement = o.support
    c.llm = o.confidence
    c.score = round(sum(getattr(c, k) * w for k, w in W.items()), 3)
    alert = any(i.startswith("[alerte]") for i in o.issues)
    if c.score >= settings.auto_threshold and not alert:
        o.decision = "retenue"
    elif c.score >= settings.review_threshold:
        o.decision = "a_valider"
    else:
        o.decision = "a_revoir"


def verify(fiche: FicheLLM, synth: Synthese, articles: list[Article], obls: list[Obligation], doc: DocumentText,
           seg: Segmented, settings: Settings, llm: MistralClient) -> tuple[list[Obligation], list[Controle]]:
    controls: list[Controle] = []
    # citations de la fiche, de la synthèse, des mesures
    groups = [("fiche", fiche.citations), ("synthèse", synth.citations)]
    groups += [(f"impact « {i.description[:40]} »", i.citations) for i in synth.impacts]
    groups += [(f"art. {a.numero} mesure {m.code}", m.citations) for a in articles for m in a.mesures]
    for label, cits in groups:
        if cits and verify_citations(cits, doc, settings.citation_min_score) < 1:
            bad = [c for c in cits if not c.verified]
            controls.append(Controle(sujet=f"Citation introuvable ({label})", gravite="alerte",
                                     description=" ; ".join(f"p.{c.page} « {c.quote[:80]} »" for c in bad),
                                     citations=bad, origine="citation"))
    for o in obls:
        o.confiance.evidence = round(verify_citations(o.citations, doc, settings.citation_min_score), 3)
        if not o.citations:
            o.issues.append("[alerte] Aucune citation.")
        elif o.confiance.evidence < 1:
            o.issues.append(f"[alerte] {sum(not c.verified for c in o.citations)} citation(s) introuvable(s).")
        o.issues += [f"[info] {a}" for a in o.ambiguities]
    obls, ctl = dedupe_and_contradictions(obls)
    controls += ctl
    if settings.contradiction_pass:
        try:
            controls += llm_controls(doc, seg, obls, llm, settings)
        except LLMUnavailable as e:
            controls.append(Controle(sujet="Contrôle LLM non réalisé", description=str(e), gravite="info", origine="format"))
    controls += coverage(articles, obls)
    controls += [Controle(sujet="Découpage", description=i, gravite="info", origine="format") for i in seg.issues]
    for o in obls:
        score(o, settings)
    return obls, controls
