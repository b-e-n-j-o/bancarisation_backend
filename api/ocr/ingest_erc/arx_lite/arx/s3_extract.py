"""Étape 3 — Extraction LLM, en 1 à quelques appels lancés en parallèle.

Modes (config.mode) :
- "single"   : UN appel renvoie fiche + synthèse + articles (actes courts) ;
- "parallel" (défaut) : un appel fiche + synthèse, et les articles répartis en lots
  (~20 000 caractères de texte source par lot), tous lancés EN MÊME TEMPS.
  Bascule automatiquement en "single" si l'acte est court.
Le temps total ≈ l'appel le plus long : c'est la longueur de la SORTIE qui coûte,
d'où des lots et des consignes de concision.
Le texte intégral reste dans chaque appel (préfixe identique, mis en cache) pour
comprendre les renvois entre articles.
Options : `samples_k` > 1 (accord entre échantillons), mode injection (réponses
préparées) pour tester hors ligne.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rapidfuzz import fuzz

from . import prompts
from .config import Settings
from .llm import LLMUnavailable, MistralClient
from .s1_ingest import DocumentText, as_prompt
from .s2_segment import Segmented
from .schemas import (Article, ArticleLLM, ArticlesBatchLLM, FicheLLM, Figure, Obligation, ObligationLLM,
                      SinglePassLLM, Synthese, SyntheseLLM)

logger = logging.getLogger("uvicorn.error")


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
def _batches(arts: list, max_chars: int) -> list[list]:
    out, cur, size = [], [], 0
    for a in arts:
        n = len(a.texte)
        if cur and size + n > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(a)
        size += n
    return out + ([cur] if cur else [])


def _samples_by_article(batch_results: list[list[ArticlesBatchLLM]]) -> dict[str, list[ArticleLLM]]:
    """[lot][échantillon] -> {numéro: [ArticleLLM par échantillon]}"""
    out: dict[str, list[ArticleLLM]] = {}
    for samples in batch_results:
        for s in samples:
            for a in s.articles:
                num = a.numero.replace("Article", "").replace("article", "").strip()
                out.setdefault(num, []).append(ArticleLLM(**a.model_dump(exclude={"numero"})))
    return out


def extract(doc: DocumentText, seg: Segmented, settings: Settings, llm: MistralClient,
            injected: dict | None = None) -> tuple[FicheLLM, Synthese, list[Article], list[Obligation], dict]:
    doc_txt, struct = as_prompt(doc), structure_hint(seg)
    key = doc.sha256
    base = dict(document=doc_txt, structure=struct)
    leaves = _leaf_articles(seg)
    k = max(1, settings.samples_k)

    def call(schema, prompt, tag, n=1, t=None):
        return llm.chat_json(schema, prompts.SYSTEM, prompt, tag=tag, n=n, temperature=t, cache_key_hint=key)

    mode = settings.mode
    if mode == "parallel" and sum(len(a.texte) for a in leaves) <= settings.single_max_chars:
        mode = "single"
    raw: dict = {"mode": "injection" if injected else mode, "appels": 0}

    if injected:
        fiche, synth = FicheLLM(**injected["fiche"]), SyntheseLLM(**injected["synthese"])
        by_art = {n: [ArticleLLM(**d)] for n, d in injected["articles"].items()}
    elif mode == "single":
        logger.info("arx_lite extraction en un seul appel (%s articles)", len(leaves))
        res = call(SinglePassLLM, prompts.SINGLE.format(**base), "single", n=k)
        if not res:
            raise LLMUnavailable("passe unique : aucune réponse exploitable")
        fiche, synth = res[0].fiche, res[0].synthese
        by_art = _samples_by_article([[ArticlesBatchLLM(articles=r.articles) for r in res]])
        raw["appels"] = 1
    else:
        batches = _batches(leaves, settings.batch_chars)
        jobs = [("fiche", None), ("synthese", None)] + [(f"lot_{i + 1}", b) for i, b in enumerate(batches)]
        logger.info(
            "arx_lite extraction parallèle : fiche, synthèse et %s lot(s) d'articles",
            len(batches),
        )

        def run_job(job):
            tag, batch = job
            if tag == "fiche":
                return tag, call(FicheLLM, prompts.FICHE.format(**base), "fiche", t=0)
            if tag == "synthese":
                return tag, call(SyntheseLLM, prompts.SYNTHESE.format(**base), "synthese", t=0)
            nums = ", ".join(a.numero for a in batch)
            return tag, call(
                ArticlesBatchLLM,
                prompts.ARTICLES_BATCH.format(**base, numeros=nums),
                f"{tag} [{nums}]",
                n=k,
            )

        with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
            results = dict(pool.map(run_job, jobs))
        f, sy = results.pop("fiche"), results.pop("synthese")
        fiche = f[0] if f else FicheLLM()
        synth = sy[0] if sy else SyntheseLLM()
        by_art = _samples_by_article(list(results.values()))
        raw["appels"] = 2 + len(batches)
        raw["lots"] = [[a.numero for a in b] for b in batches]

    # compléments déterministes si le LLM n'a rien trouvé
    fiche.reference = fiche.reference or seg.reference
    fiche.date_signature = fiche.date_signature or seg.signature_date
    fiche.lieu_signature = fiche.lieu_signature or seg.signature_lieu

    articles: list[Article] = []
    obligations: list[Obligation] = []
    raw["articles"] = {}
    missing = []
    for a in leaves:
        samples = by_art.get(a.numero, [])
        raw["articles"][a.numero] = [x.model_dump() for x in samples]
        art = Article(numero=a.numero, titre=a.titre, parent=a.parent, niveau=a.niveau,
                      page_debut=a.page_debut, page_fin=a.page_fin, texte=a.texte)
        if samples:
            ref, supported, agreement = consolidate_article(samples) if len(samples) > 1 else \
                (samples[0], [(o, 1.0) for o in samples[0].obligations], 1.0)
            art = Article(**{**ref.model_dump(), "obligations": []}, numero=a.numero, parent=a.parent,
                          niveau=a.niveau, page_debut=a.page_debut, page_fin=a.page_fin, texte=a.texte,
                          support=agreement)
            art.titre = a.titre or ref.titre
            for o, sup in supported:
                obligations.append(Obligation(**o.model_dump(), article=a.numero, support=sup))
                art.obligations.append(o)
        else:
            missing.append(a.numero)
        articles.append(art)
    raw["articles_sans_reponse"] = missing
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
