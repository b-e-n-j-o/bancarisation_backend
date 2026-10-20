"""Orchestration. Chaque étape écrit sa sortie dans runs/<doc>_<horodatage>/ :

    01_document.json   texte par page (natif / OCR), lignes d'en-tête/pied retirées
    02_segments.json   découpage : en-tête, VU, CONSIDÉRANT, articles, signature, figures
    03_raw.json        réponses LLM brutes (k échantillons par article)
    arrete.json        résultat complet (format défini dans schemas.ArreteAnalyse)
    rapport.md         rendu lisible, dans l'ordre de la future page « Arrêté »
    llm_calls.jsonl    journal des appels
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from .config import Settings
from .llm import LLMUnavailable, MistralClient
from .s1_ingest import ingest
from .s2_segment import segment
from .s3_extract import extract, read_figures
from .s4_verify import verify
from .s5_schedule import schedule
from .s6_export import write_report
from .schemas import ArreteAnalyse


def _dump(path: Path, obj) -> None:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def run(pdf_path: Path, settings: Settings, anchors: dict[str, str] | None = None,
        inject: Path | None = None, log=print) -> tuple[ArreteAnalyse, Path]:
    run_dir = settings.runs_dir / f"{pdf_path.stem[:40]}_{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    llm = MistralClient(settings, run_dir)

    log("1/6 ingestion")
    doc, issues = ingest(pdf_path, settings, llm)
    _dump(run_dir / "01_document.json", doc)

    log("2/6 découpage")
    seg = segment(doc)
    _dump(run_dir / "02_segments.json", seg)

    log("3/6 extraction (fiche, synthèse, articles)")
    injected = json.loads(Path(inject).read_text(encoding="utf-8")) if inject else None
    try:
        fiche, synth, articles, obls, raw = extract(doc, seg, settings, llm, injected)
    except LLMUnavailable as e:
        raise SystemExit(f"Extraction impossible sans API ({e}). --inject permet de tester hors ligne.")
    _dump(run_dir / "03_raw.json", raw)
    issues += read_figures(seg.figures, pdf_path, settings, llm)

    log("4/6 vérification (citations, doublons, contradictions, confiance)")
    obls, controls = verify(fiche, synth, articles, obls, doc, seg, settings, llm)
    if raw.get("articles_sans_reponse"):
        from .schemas import Controle
        controls.append(Controle(sujet="Articles non analysés par le LLM", gravite="alerte", origine="couverture",
                                 description="Aucune réponse pour : " + ", ".join(raw["articles_sans_reponse"]),
                                 articles=raw["articles_sans_reponse"]))

    log("5/6 échéancier")
    rows, used_anchors = schedule(obls, fiche, anchors or {})

    log("6/6 exports")
    stats = {
        "mode": raw.get("mode"), "appels_llm_extraction": raw.get("appels"),
        "pages": doc.n_pages, "articles": len(articles), "visas": len(fiche.visas) or len(seg.visas),
        "especes": synth.nb_especes, "mesures": sum(len(a.mesures) for a in articles), "obligations": len(obls),
        "obligations_par_categorie": dict(Counter(o.categorie for o in obls)),
        "decisions": dict(Counter(o.decision for o in obls)),
        "echeances_datees": sum(1 for r in rows if r.calculable),
        "controles": dict(Counter(c.gravite for c in controls)),
        "issues": issues,
    }
    res = ArreteAnalyse(document=str(pdf_path), sha256=doc.sha256, settings=settings.public_dict(), ancres=used_anchors,
                        fiche=fiche, synthese=synth, articles=articles, obligations=obls, echeancier=rows,
                        figures=seg.figures, controles=controls, stats=stats)
    _dump(run_dir / "arrete.json", res)
    write_report(res, run_dir / "rapport.md")
    log(f"→ {run_dir}  {json.dumps({k: v for k, v in stats.items() if k != 'issues'}, ensure_ascii=False)}")
    return res, run_dir
