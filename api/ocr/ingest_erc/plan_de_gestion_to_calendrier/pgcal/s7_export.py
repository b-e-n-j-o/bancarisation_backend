"""Étape 7 — Exports : calendar.json (pour l'application), occurrences.csv,
review.md (rapport de relecture lisible : ce qui est intégré, ce qui attend
une validation, pourquoi, avec les citations)."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from .s3_grids import grids_as_text
from .s6_expand import horizon_end
from .schemas import CalendarResult, Grid

MONTHS = ["", "janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc"]
LABEL = {"integre": "intégré", "a_valider": "à valider", "a_revoir": "à revoir",
         "declencheur": "déclencheur", "a_preciser": "à préciser"}


def write_csv(res: CalendarResult, path: Path) -> None:
    cols = ["occ_id", "rule_id", "action", "sous_action", "ug", "annee_rel", "annee", "fin_rel", "mois",
            "passage", "optionnel", "hypothese", "decision", "confiance", "pages"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for o in res.occurrences:
            w.writerow([o.occ_id, o.rule_id, o.action_code, o.sub_action, "+".join(o.ug_codes) or "site",
                        "" if o.rel_year is None else f"N+{o.rel_year}" if o.rel_year else "N",
                        o.year or "", "" if o.span_end_rel is None else f"N+{o.span_end_rel}",
                        ",".join(MONTHS[m] for m in o.months), o.pass_label, "oui" if o.optional else "",
                        o.hypothesis, LABEL[o.decision], o.confidence, ",".join(map(str, o.source_pages))])


def write_review(res: CalendarResult, grids: list[Grid], path: Path) -> None:
    f = res.frame
    L = [f"# Calendrier du plan de gestion — {Path(res.document).name}", ""]
    ny = res.settings.get("n_year_override") or f.n_year or f.n_year_from_grid
    L += ["## Cadre", "",
          f"- Durée de gestion : **{f.duration_years or '?'} ans** — horizon N+{horizon_end(f)}",
          f"- N = {f.n_definition or '?'} — année retenue : **{ny or 'inconnue'}** "
          f"(texte : {f.n_year or '—'}, grilles : {f.n_year_from_grid or '—'}, forcée : {res.settings.get('n_year_override') or '—'})",
          f"- Opérateur : {f.operator or '—'}",
          f"- UG : {', '.join(f'{u.code} ({u.surface_m2:g} m²)' if u.surface_m2 else u.code for u in f.ugs) or '—'}", ""]
    L += ["| Action | Intitulé | UG |", "|---|---|---|"]
    L += [f"| {a.code} | {a.title} | {'site' if a.site_wide else '+'.join(a.ug_codes) or '—'} |" for a in f.actions]
    L.append("")

    dec = Counter(r.decision for r in res.rules)
    occ_dec = Counter(o.decision for o in res.occurrences)
    L += ["## Synthèse", "",
          "| | Règles | Occurrences |", "|---|---|---|"]
    L += [f"| {LABEL[d]} | {dec.get(d, 0)} | {occ_dec.get(d, 0)} |" for d in LABEL]
    L.append("")

    L += ["## Règles", ""]
    for r in sorted(res.rules, key=lambda r: (r.action_code, r.rule_id)):
        d = r.confidence_detail
        grid = "—" if d.grid is None else f"{d.grid:.2f}"
        L.append(f"### {r.rule_id} — {r.sub_action} ({'+'.join(r.ug_codes) or 'site'}) — **{LABEL[r.decision]}** · {d.score:.2f}")
        L.append(f"`{r.kind}` · fréquence : « {r.frequency_text or '—'} » · période : « {r.window_text or '—'} »")
        L.append(f"Confiance — citations {d.evidence:.2f} · accord échantillons {d.agreement:.2f} · grille {grid} "
                 f"· précision {d.specificity:.2f} · auto-évaluation {d.llm:.2f}")
        segs = "; ".join(
            (f"années {s.years}" if s.years else
             f"N+{s.start or 0}→{'fin' if s.end is None else f'N+{s.end}'} pas {s.step_min}"
             + (f"-{s.step_max}" if s.step_max > s.step_min else "")
             + (f" ({s.count_over_period} fois)" if s.count_over_period else ""))
            for s in r.segments) or "—"
        passes = "; ".join(f"{p.label or 'passage'} [{', '.join(MONTHS[m] for m in p.months) or '?'}]"
                           + (" (optionnel)" if p.optional else "") for p in r.passes) or "—"
        L.append(f"Segments : {segs} · Passages : {passes}")
        if r.trigger:
            L.append(f"Déclencheur : {r.trigger}")
        for i in r.issues:
            L.append(f"- {i}")
        for c in r.critic_changes[:5]:
            L.append(f"- [relecture] {c}")
        for c in r.citations[:3]:
            L.append(f"> p.{c.page} — « {c.quote} »")
        L.append("")

    if f.issues or res.issues:
        L += ["## Points d'attention globaux", ""] + [f"- {i}" for i in f.issues + res.issues] + [""]
    if f.notes:
        L += ["## Incohérences relevées dans le document", ""] + [f"- {n}" for n in f.notes] + [""]
    L += ["## Grilles lues", "", "```", grids_as_text(grids), "```", ""]
    path.write_text("\n".join(L), encoding="utf-8")
