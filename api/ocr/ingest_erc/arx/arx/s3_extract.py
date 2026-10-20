"""Étape 3 — Extraction LLM : fiche, synthèse, puis chaque article (k échantillons).

- Le texte intégral de l'acte est le préfixe de chaque requête (identique -> mis en
  cache via `prompt_cache_key`) : un article se comprend avec ses renvois.
- Une requête par article « feuille » (et par article parent s'il a un texte propre).
- Les k échantillons d'un article sont consolidés : une obligation présente dans
  peu d'échantillons a un `support` faible, qui pèse dans la confiance.
- Mode injection : des réponses préparées (jeu de référence, autre système) peuvent
  remplacer le LLM pour tester toute la chaîne hors ligne.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from rapidfuzz import fuzz

from . import prompts
from .config import Settings
from .llm import LLMUnavailable, MistralClient
from .s1_ingest import DocumentText, as_prompt
from .s2_segment import Segmented
from .schemas import (Article, ArticleLLM, FicheLLM, Figure, Obligation, ObligationLLM, Synthese,
                      SyntheseLLM)


def structure_hint(seg: Segmented) -> str:
    lines = [f"Référence repérée : {seg.reference or '?'} ; signature : {seg.signature_lieu or '?'}, "
             f"{seg.signature_date or '?'} ; {len(seg.visas)} VU ; {len(seg.considerants)} CONSIDÉRANT"]
    for a in seg.articles:
        lines.append(f"{'  ' * (a.niveau - 1)}Article {a.numero} — {a.titre} (p.{a.page_debut}-{a.page_fin})")
    if seg.figures:
        lines.append("Figures : " + " ; ".join(f"fig.{f.numero} p.{f.page} « {f.legende} »" for f in seg.figures))
    return "\n".join(lines)


def _leaf_articles(seg: Segmented) -> list:
    """Articles à analyser : feuilles + parents qui ont un texte propre (introduction)."""
    out = []
    for a in seg.articles:
        has_children = any(b.parent == a.numero for b in seg.articles)
        body = re.sub(r"\[PAGE \d+\]", "", a.texte).strip()
        if not has_children or len(body) > 40:
            out.append(a)
    return out


# ----------------------------------------------------------------- consolidation
def _ob_key(o: ObligationLLM) -> str:
    return re.sub(r"\s+", " ", o.intitule.lower()).strip()


def consolidate_article(samples: list[ArticleLLM]) -> tuple[ArticleLLM, list[tuple[ObligationLLM, float]], float]:
    """Retourne l'échantillon de référence, ses obligations avec leur support, et l'accord sur les catégories."""
    k = len(samples)
    base = max(samples, key=lambda s: (len(s.obligations), len(s.resume)))
    supported = []
    for o in base.obligations:
        hits = 1
        for s in samples:
            if s is base:
                continue
            if any(fuzz.token_set_ratio(_ob_key(o), _ob_key(p)) >= 75 and o.echeance.kind == p.echeance.kind
                   for p in s.obligations):
                hits += 1
        supported.append((o, hits / k))
    # obligations vues dans d'autres échantillons mais absentes de la référence
    for s in samples:
        if s is base:
            continue
        for p in s.obligations:
            if not any(fuzz.token_set_ratio(_ob_key(p), _ob_key(o)) >= 75 for o, _ in supported):
                hits = sum(1 for t in samples if any(fuzz.token_set_ratio(_ob_key(p), _ob_key(q)) >= 75
                                                       for q in t.obligations))
                supported.append((p, hits / k))
    cats = Counter(c for s in samples for c in set(s.categories))
    base.categories = [c for c, n in cats.items() if n * 2 >= k] or base.categories
    agreement = sum(1 for s in samples if set(s.categories) == set(base.categories)) / k
    return base, supported, agreement


# ----------------------------------------------------------------- étape
def extract(doc: DocumentText, seg: Segmented, settings: Settings, llm: MistralClient,
            injected: dict | None = None) -> tuple[FicheLLM, Synthese, list[Article], list[Obligation], dict]:
    doc_txt, struct = as_prompt(doc), structure_hint(seg)
    raw: dict = {"articles": {}}
    key = doc.sha256

    def call(schema, prompt, tag, n=1, t=None):
        return llm.chat_json(schema, prompts.SYSTEM, prompt, tag=tag, n=n, temperature=t, cache_key_hint=key)

    base = dict(document=doc_txt, structure=struct)
    if injected:
        fiche = FicheLLM(**injected["fiche"])
        synth = SyntheseLLM(**injected["synthese"])
    else:
        fiche = (call(FicheLLM, prompts.FICHE.format(**base), "fiche", t=0) or [FicheLLM()])[0]
        synth = (call(SyntheseLLM, prompts.SYNTHESE.format(**base), "synthese", t=0) or [SyntheseLLM()])[0]
    # compléments déterministes si le LLM n'a rien trouvé
    fiche.reference = fiche.reference or seg.reference
    fiche.date_signature = fiche.date_signature or seg.signature_date
    fiche.lieu_signature = fiche.lieu_signature or seg.signature_lieu

    articles: list[Article] = []
    obligations: list[Obligation] = []
    for a in _leaf_articles(seg):
        if injected:
            data = injected["articles"].get(a.numero)
            samples = [ArticleLLM(**data)] if data else []
        else:
            samples = call(ArticleLLM, prompts.ARTICLE.format(**base, numero=a.numero, titre=a.titre, texte=a.texte),
                           f"art_{a.numero}", n=settings.samples_k)
        raw["articles"][a.numero] = [s.model_dump() for s in samples]
        art = Article(numero=a.numero, titre=a.titre, parent=a.parent, niveau=a.niveau,
                      page_debut=a.page_debut, page_fin=a.page_fin, texte=a.texte)
        if samples:
            ref, supported, agreement = consolidate_article(samples)
            art = Article(**{**ref.model_dump(), "obligations": []}, numero=a.numero, parent=a.parent,
                          niveau=a.niveau, page_debut=a.page_debut, page_fin=a.page_fin, texte=a.texte,
                          support=agreement)
            art.titre = a.titre or ref.titre
            for o, sup in supported:
                ob = Obligation(**o.model_dump(), article=a.numero, support=sup)
                obligations.append(ob)
                art.obligations.append(o)
        articles.append(art)
    # parents sans texte propre : gardés pour l'arborescence
    present = {x.numero for x in articles}
    for a in seg.articles:
        if a.numero not in present:
            articles.append(Article(numero=a.numero, titre=a.titre, parent=a.parent, niveau=a.niveau,
                                    page_debut=a.page_debut, page_fin=a.page_fin, texte=a.texte))
    order = {a.numero: i for i, a in enumerate(seg.articles)}
    articles.sort(key=lambda x: order.get(x.numero, 999))
    for i, o in enumerate(obligations, 1):
        o.id = f"OBL-{i:03d}"
    return fiche, enrich_synthese(synth, articles), articles, obligations, raw


def enrich_synthese(s: SyntheseLLM, articles: list[Article]) -> Synthese:
    out = Synthese(**s.model_dump())
    out.nb_especes = len(s.especes)
    out.especes_par_groupe = dict(Counter(e.groupe for e in s.especes))
    out.especes_par_nature = dict(Counter(n for e in s.especes for n in e.natures))
    out.mesures_par_type = dict(Counter(m.type for a in articles for m in a.mesures))
    return out


def read_figures(figures: list[Figure], pdf_path: Path, settings: Settings, llm: MistralClient) -> list[str]:
    """Optionnel (désactivé par défaut) : lecture des légendes / valeurs des figures par le modèle vision."""
    if not settings.figures_vision:
        return []
    import io
    import pdfplumber
    from pydantic import BaseModel

    class FigLLM(BaseModel):
        contenu: str = ""
        valeurs: list[str] = []

    issues = []
    for f in figures:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                img = pdf.pages[f.page - 1].to_image(resolution=130).original
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            r = llm.vision_json(FigLLM, prompts.SYSTEM, prompts.FIGURE.format(numero=f.numero, legende=f.legende),
                                buf.getvalue(), tag=f"fig_{f.numero}")
            if r:
                f.contenu, f.valeurs = r.contenu, r.valeurs
        except LLMUnavailable as e:
            issues.append(f"Figures non lues ({e}).")
            break
    return issues
