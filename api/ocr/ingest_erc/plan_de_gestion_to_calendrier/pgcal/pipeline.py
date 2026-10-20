"""Orchestration : enchaîne les 7 étapes et écrit la sortie de CHAQUE étape
dans le dossier du run (runs/<doc>_<horodatage>/), pour savoir à tout moment
ce que le système a vu, compris et décidé.

    01_document.json   texte par page (natif / OCR)
    02_map.json        rôles des pages, codes repérés, indices de durée / N
    03_grids.json/.txt grilles lues (géométrie + couleurs + vision)
    04_frame.json      cadre du plan (durée, N, UG, actions)
    04_samples.json    échantillons LLM bruts par action
    05_rules.json      règles vérifiées + détail du score de confiance
    calendar.json      résultat complet (cadre, règles, occurrences)
    occurrences.csv    une ligne par occurrence
    review.md          rapport de relecture
    llm_calls.jsonl    journal des appels API (modèle, durée, tokens, cache)
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from .config import Settings
from .llm import LLMUnavailable, MistralClient
from .s1_ingest import ingest
from .s2_locate import locate
from .s3_grids import grids_as_text, read_grids
from .s4_extract import build_context, complete_catalog, extract_frame, extract_rules
from .s5_verify import verify
from .s6_expand import expand
from .s7_export import write_csv, write_review
from .schemas import CalendarResult, Frame, Rule


def _dump(path: Path, obj) -> None:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    elif isinstance(obj, list) and obj and hasattr(obj[0], "model_dump"):
        obj = [o.model_dump() for o in obj]
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def run(pdf_path: Path, settings: Settings, rules_json: Path | None = None, log=print) -> tuple[CalendarResult, Path]:
    run_dir = settings.runs_dir / f"{pdf_path.stem[:40]}_{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    llm = MistralClient(settings, run_dir)
    issues: list[str] = []

    log(f"1/7 ingestion — {pdf_path.name}")
    doc, iss = ingest(pdf_path, settings, llm)
    issues += iss
    n_ocr = sum(1 for p in doc.pages if p.source == "ocr")
    log(f"   {doc.n_pages} page(s), {n_ocr} lue(s) par OCR, {len(iss)} remarque(s)")
    _dump(run_dir / "01_document.json", doc)

    log("2/7 repérage des pages")
    dm, iss = locate(doc, settings, llm)
    issues += iss
    log(f"   {len(dm.pg_pages)} page(s) du plan, {len(dm.code_candidates)} code(s) candidat(s)")
    _dump(run_dir / "02_map.json", dm)

    mode = "vision" if settings.grid_vision_crosscheck else "géométrie seule"
    log(f"3/7 lecture des grilles ({mode})")
    grids, iss = read_grids(doc, dm, settings, llm, log=log)
    issues += iss
    log(f"   {len(grids)} grille(s)")
    _dump(run_dir / "03_grids.json", grids)
    (run_dir / "03_grids.txt").write_text(grids_as_text(grids), encoding="utf-8")

    log("4/7 extraction (cadre + règles)")
    doc_txt, grids_txt = build_context(doc, dm, grids, settings)
    if rules_json:   # règles fournies (jeu de référence, autre système) : on saute le LLM
        data = json.loads(Path(rules_json).read_text(encoding="utf-8"))
        frame = Frame(**data["frame"])
        votes = Counter(g.n_year for g in grids if g.n_year is not None)
        frame.n_year_from_grid = votes.most_common(1)[0][0] if votes else None
        complete_catalog(frame, grids, dm)
        rules = [Rule(**r) for r in data["rules"]]
        per_code: Counter = Counter()
        for r in rules:
            per_code[r.action_code] += 1
            r.rule_id = r.rule_id or f"{r.action_code}-{per_code[r.action_code]:02d}"
            r.confidence_detail.agreement = 1.0
        issues.append(f"Règles injectées depuis {rules_json} (pas d'extraction LLM).")
        log(f"   règles injectées : {len(rules)}")
    else:
        try:
            frame = extract_frame(doc_txt, grids_txt, grids, dm, llm, doc.sha256)
            rules, samples = extract_rules(frame, doc_txt, grids_txt, settings, llm, doc.sha256)
            _dump(run_dir / "04_samples.json", samples)
            log(f"   cadre : durée {frame.duration_years or '?'} ans, "
                f"N={frame.n_year or frame.n_year_from_grid or '?'}, "
                f"{len(frame.actions)} action(s), {len(rules)} règle(s)")
        except LLMUnavailable as e:
            raise SystemExit(f"Extraction impossible sans API ({e}). Utiliser --rules-json pour tester hors ligne.")
    _dump(run_dir / "04_frame.json", frame)

    log("5/7 vérification + confiance")
    rules, iss = verify(rules, frame, grids, doc, doc_txt, settings, llm)
    issues += iss
    log(f"   décisions : {dict(Counter(r.decision for r in rules))}")
    _dump(run_dir / "05_rules.json", rules)

    log("6/7 déroulement")
    occs = expand(rules, frame, settings)
    log(f"   {len(occs)} occurrence(s)")

    log("7/7 exports")
    stats = {
        "pages": doc.n_pages, "pages_plan": len(dm.pg_pages), "grilles": len(grids),
        "actions": len(frame.actions), "regles": len(rules), "occurrences": len(occs),
        "decisions_regles": dict(Counter(r.decision for r in rules)),
        "decisions_occurrences": dict(Counter(o.decision for o in occs)),
    }
    res = CalendarResult(document=str(pdf_path), settings=settings.public_dict(), frame=frame, rules=rules,
                         occurrences=occs, issues=issues, stats=stats)
    _dump(run_dir / "calendar.json", res)
    write_csv(res, run_dir / "occurrences.csv")
    write_review(res, grids, run_dir / "review.md")
    log(f"→ {run_dir}  ({json.dumps(stats, ensure_ascii=False)})")
    return res, run_dir
