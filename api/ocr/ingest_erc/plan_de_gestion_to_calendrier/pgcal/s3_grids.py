"""Étape 3 — Lecture des grilles temporelles (le point dur des plans de gestion).

Grilles visées :
- pluriannuelles : colonnes N, N+1… / 2025, 2026… / plages « N+6 à N+10 » ;
- mensuelles : colonnes Janvier…Décembre, souvent codées UNIQUEMENT par la
  couleur de fond des cellules (aucune trace dans le texte extrait).

Deux lectures indépendantes, puis réconciliation :
1. géométrie (PDF natif) : cellules de tableau pdfplumber + mots + rectangles
   remplis -> colonnes, lignes, croix, bandes fusionnées, cellules colorées ;
2. vision (rendu PNG -> modèle multimodal) : indispensable pour les scans,
   et utilisée en double lecture pour mesurer l'accord.
Sortie : `Grid` par grille, plus une transcription texte destinée aux prompts.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

import pdfplumber
from rapidfuzz import fuzz

from . import prompts
from .config import Settings
from .llm import LLMUnavailable, MistralClient
from .s1_ingest import render_page_png
from .schemas import DocumentMap, DocumentText, Grid, GridCell, GridColumn, GridRow, VisionPageLLM

# ----------------------------------------------------------------- libellés
MONTHS = {"jan": 1, "fev": 2, "mar": 3, "avr": 4, "mai": 5, "juin": 6, "juil": 7, "aou": 8,
          "sep": 9, "oct": 10, "nov": 11, "dec": 12}
MARKS = {"x", "✓", "✔", "●", "•", "■", "◼", "*", "oui", "xx"}
CODE_PREFIX_RE = re.compile(r"^\s*([A-Z]{1,4}\s?-?\d{1,2}(?:\.\d{1,2})?)\b")

_SEP = r"\s*(?:à|a|-|–|au)\s*"
REL_RANGE = re.compile(r"(?<![A-Za-zÀ-ÿ])N\s*(?:\+\s*(\d{1,2}))?" + _SEP + r"N\s*\+\s*(\d{1,2})")
REL_ONE = re.compile(r"(?<![A-Za-zÀ-ÿ])N(?:\s*\+\s*(\d{1,2}))?(?![A-Za-zÀ-ÿ\d])")
YEAR_RANGE = re.compile(r"((?:19|20)\d{2})" + _SEP + r"((?:19|20)\d{2})")
YEAR_ONE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def parse_label(text: str) -> dict | None:
    """Interprète un libellé d'en-tête. None si ce n'est pas un en-tête temporel."""
    t = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    if not t or len(t) > 40:
        return None
    low = _strip_accents(t.lower()).strip(" .")
    for k, m in MONTHS.items():
        if low.startswith(k) and len(low) <= 10:
            return {"axis": "month", "month": m}
    out: dict = {}
    rest = t
    m = REL_RANGE.search(rest)
    if m:
        out["rel_start"], out["rel_end"] = int(m.group(1) or 0), int(m.group(2))
        rest = rest.replace(m.group(0), " ")
    else:
        m = REL_ONE.search(rest)
        if m:
            out["rel_start"] = out["rel_end"] = int(m.group(1) or 0)
            rest = rest.replace(m.group(0), " ", 1)
    m = YEAR_RANGE.search(rest)
    if m:
        out["year_start"], out["year_end"] = int(m.group(1)), int(m.group(2))
        rest = rest.replace(m.group(0), " ")
    else:
        m = YEAR_ONE.search(rest)
        if m:
            out["year_start"] = out["year_end"] = int(m.group(1))
            rest = rest.replace(m.group(0), " ", 1)
    if not out or len(re.sub(r"[\W_àa]", "", rest)) > 3:   # reste trop de texte : pas un en-tête
        return None
    out["axis"] = "rel_year" if "rel_start" in out else "abs_year"
    return out


# ----------------------------------------------------------------- couleurs
def _hex(color) -> str | None:
    if color is None or isinstance(color, str):
        return None
    if isinstance(color, (int, float)):
        color = (color, color, color)
    color = tuple(color)
    if len(color) == 1:
        color = color * 3
    if len(color) == 4:  # CMYK -> RGB
        c, m, y, k = color
        color = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    if len(color) != 3:
        return None
    r, g, b = (max(0.0, min(1.0, float(v))) for v in color)
    if min(r, g, b) > 0.97 or max(r, g, b) < 0.15:   # blanc ou noir (bordures)
        return None
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _fills(page) -> list[tuple[tuple[float, float, float, float], str]]:
    out = []
    for r in page.rects:
        if not r.get("fill"):
            continue
        h = _hex(r.get("non_stroking_color"))
        if h and (r["x1"] - r["x0"]) > 3 and (r["bottom"] - r["top"]) > 3:
            out.append(((r["x0"], r["top"], r["x1"], r["bottom"]), h))
    return out


def _overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _cell_fill(bbox, fills) -> str | None:
    best, best_a = None, 0.0
    for fb, h in fills:
        a = _overlap(bbox, fb)
        if a > best_a:
            best, best_a = h, a
    return best if best and best_a >= 0.5 * _area(bbox) else None


# ----------------------------------------------------------------- géométrie
class _Cell:
    __slots__ = ("bbox", "text", "fill")

    def __init__(self, bbox, text, fill):
        self.bbox, self.text, self.fill = bbox, text, fill

    @property
    def x0(self): return self.bbox[0]
    @property
    def x1(self): return self.bbox[2]
    @property
    def top(self): return self.bbox[1]
    @property
    def bottom(self): return self.bbox[3]
    @property
    def ymid(self): return (self.bbox[1] + self.bbox[3]) / 2


def _cells_with_text(page, table, words, fills) -> list[_Cell]:
    cells = [_Cell(tuple(b), "", None) for b in table.cells]
    buckets: dict[int, list] = {}
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        best, best_a = None, None
        for i, c in enumerate(cells):
            if c.x0 - 1 <= cx <= c.x1 + 1 and c.top - 1 <= cy <= c.bottom + 1:
                a = _area(c.bbox)
                if best_a is None or a < best_a:
                    best, best_a = i, a
        if best is not None:
            buckets.setdefault(best, []).append(w)
    for i, c in enumerate(cells):
        ws = sorted(buckets.get(i, []), key=lambda w: (round(w["top"] / 3), w["x0"]))
        c.text = " ".join(w["text"] for w in ws).strip()
        c.fill = _cell_fill(c.bbox, fills)
    return cells


def _header_bands(cells: list[_Cell]) -> list[dict]:
    """Regroupe les cellules d'en-tête temporel en bandes (une bande = un axe de grille)."""
    hdr = []
    for c in cells:
        lab = parse_label(c.text) if c.text else None
        if lab:
            hdr.append((c, lab))
    bands: list[dict] = []
    for axis_group in ("month", "year"):
        group = [(c, l) for c, l in hdr if (l["axis"] == "month") == (axis_group == "month")]
        group.sort(key=lambda cl: (cl[0].top, cl[0].x0))
        # rangées par position verticale, puis fusion des rangées empilées (N au-dessus de 2025)
        rows: list[list] = []
        for c, l in group:
            if rows and abs(rows[-1][0][0].top - c.top) < 4:
                rows[-1].append((c, l))
            else:
                rows.append([(c, l)])
        stacks: list[list[list]] = []
        for r in rows:
            if stacks:
                prev = [cl for row_ in stacks[-1] for cl in row_]
                gap = min(c.top for c, _ in r) - max(c.bottom for c, _ in stacks[-1][-1])
                xs_overlap = sum(any(min(a.x1, b.x1) - max(a.x0, b.x0) > 0.5 * min(a.x1 - a.x0, b.x1 - b.x0)
                                     for b, _ in prev) for a, _ in r)
                if gap < 30 and xs_overlap >= max(1, len(r) // 2):
                    stacks[-1].append(r)
                    continue
            stacks.append([r])
        for st in stacks:
            cols: list[dict] = []
            for r in st:
                for c, l in r:
                    tgt = next((k for k in cols if min(k["x1"], c.x1) - max(k["x0"], c.x0)
                                > 0.5 * min(k["x1"] - k["x0"], c.x1 - c.x0)), None)
                    if tgt is None:
                        tgt = {"x0": c.x0, "x1": c.x1, "labels": [], "cells": []}
                        cols.append(tgt)
                    tgt["labels"].append((c.text, l))
                    tgt["cells"].append(c)
                    tgt["x0"], tgt["x1"] = min(tgt["x0"], c.x0), max(tgt["x1"], c.x1)
            min_cols = 6 if axis_group == "month" else 4
            if len(cols) >= min_cols:
                cols.sort(key=lambda k: k["x0"])
                bands.append({"axis_group": axis_group, "cols": cols,
                              "top": min(c.top for r in st for c, _ in r),
                              "bottom": max(c.bottom for r in st for c, _ in r)})
    return bands


def _make_columns(band: dict) -> tuple[list[GridColumn], int | None, str]:
    cols, n_votes = [], Counter()
    for k in band["cols"]:
        merged: dict = {}
        for _, l in k["labels"]:   # une cellule d'en-tête peut être coupée en deux (« N+6 à » / « N+10 »)
            for kk, v in l.items():
                if v is None or kk == "axis":
                    continue
                if kk in merged and kk.endswith("_start"):
                    merged[kk] = min(merged[kk], v)
                elif kk in merged and kk.endswith("_end"):
                    merged[kk] = max(merged[kk], v)
                else:
                    merged[kk] = v
        label = " / ".join(t for t, _ in k["labels"])
        axis = "month" if "month" in merged else ("rel_year" if "rel_start" in merged else "abs_year")
        cols.append(GridColumn(label=label, axis=axis, x0=k["x0"], x1=k["x1"],
                               rel_start=merged.get("rel_start"), rel_end=merged.get("rel_end"),
                               year_start=merged.get("year_start"), year_end=merged.get("year_end"),
                               month=merged.get("month")))
        if merged.get("rel_start") is not None and merged.get("year_start") is not None:
            n_votes[merged["year_start"] - merged["rel_start"]] += 1
    axis = Counter(c.axis for c in cols).most_common(1)[0][0]
    if axis != "month" and any(c.rel_start is not None for c in cols):
        axis = "rel_year"
    # bornes au milieu entre colonnes voisines : les cellules d'en-tête sont souvent plus étroites que le corps
    centers = [(c.x0 + c.x1) / 2 for c in cols]
    for i, c in enumerate(cols):
        half = (c.x1 - c.x0) / 2
        c.x0 = (centers[i - 1] + centers[i]) / 2 if i > 0 else c.x0 - 0.25 * half
        c.x1 = (centers[i] + centers[i + 1]) / 2 if i < len(cols) - 1 else c.x1 + 0.25 * half
    return cols, (n_votes.most_common(1)[0][0] if n_votes else None), axis


def _covered(cols: list[GridColumn], x0: float, x1: float) -> list[int]:
    out = []
    for i, c in enumerate(cols):
        ov = min(c.x1, x1) - max(c.x0, x0)
        if ov > 0.5 * (c.x1 - c.x0):
            out.append(i)
    return out


def _clean_label(t: str) -> str:
    toks = t.split()
    if toks and sum(len(x) <= 2 for x in toks) / len(toks) >= 0.6:   # texte vertical éclaté
        return ""
    t = re.sub(r"(?<!\S)[b-zB-Z](?!\S)", " ", t)                       # lettres isolées parasites
    return re.sub(r"\s+", " ", t).strip(" -")


def _row_labels(cells: list[_Cell], grid_x0: float, top: float, bottom: float, fiche_code: str | None):
    groups: dict[int, list[_Cell]] = {}
    for c in cells:
        if c.x1 > grid_x0 + 2 or not c.text:
            continue
        ov = min(c.bottom, bottom) - max(c.top, top)
        if ov > 0.3 * min(c.bottom - c.top, bottom - top):
            groups.setdefault(round(c.x0 / 4), []).append(c)
    texts = []
    for _, cs in sorted(groups.items()):
        t = _clean_label(" ".join(c.text for c in sorted(cs, key=lambda c: c.top)))
        if t:
            texts.append(t)
    code = None
    for t in texts:
        m = CODE_PREFIX_RE.match(t)
        if m:
            code = re.sub(r"[\s-]", "", m.group(1))
            break
    code = code or fiche_code
    rest = [t for t in texts if not (CODE_PREFIX_RE.match(t) and len(t) <= 8)]
    sub = rest[-1] if rest else ""
    action = rest[0] if len(rest) > 1 else ""
    return code, action, sub


def _classify_cell(text: str, ncols: int, fill: str | None, neutral: set[str]) -> str | None:
    t = text.strip()
    if t and _strip_accents(t.lower()) in MARKS:
        return "mark"
    if t and (ncols >= 2 or len(t) > 3):
        return "band"
    if fill and fill not in neutral:
        return "fill"
    return None


def _dedupe_row(row: GridRow) -> None:
    """Supprime les doublons : sous-cellules colorées d'une bande, croix répétées sur une même colonne."""
    band_cols = {i for c in row.cells if c.kind == "band" for i in c.cols}
    seen, out = set(), []
    for c in row.cells:
        if c.kind == "fill" and set(c.cols) <= band_cols:
            continue
        if c.kind in ("mark", "fill"):
            cols = [i for i in c.cols if i not in seen and i not in band_cols]
            if not cols:
                continue
            seen.update(cols)
            c = c.model_copy(update={"cols": cols})
        out.append(c)
    row.cells = out


def parse_page_geometry(pdf: pdfplumber.PDF, number: int, fiche_code: str | None) -> list[Grid]:
    page = pdf.pages[number - 1]
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    fills = _fills(page)
    grids: list[Grid] = []
    for ti, table in enumerate(page.find_tables()):
        cells = _cells_with_text(page, table, words, fills)
        bands = _header_bands(cells)
        bands.sort(key=lambda b: b["top"])
        for bi, band in enumerate(bands):
            cols, n_year, axis = _make_columns(band)
            gx0 = band["cols"][0]["x0"]          # bord gauche réel de l'en-tête (avant élargissement)
            gx1 = cols[-1].x1
            header_ids = {id(c) for k in band["cols"] for c in k["cells"]}
            next_top = min([b["top"] for b in bands if b["top"] > band["bottom"] + 1] + [table.bbox[3] + 1])
            header_fills = Counter(c.fill for k in band["cols"] for c in k["cells"])
            rows: dict[tuple, GridRow] = {}

            if axis == "month":
                # mois actifs = cellules d'en-tête dont la couleur se distingue de la couleur dominante
                modal, modal_n = header_fills.most_common(1)[0]
                active = [i for i, k in enumerate(band["cols"]) if any(c.fill != modal for c in k["cells"])]
                if active and modal_n >= len(cols) / 2:
                    code, action, sub = _row_labels(cells, gx0, band["top"], band["bottom"], fiche_code)
                    row = GridRow(code=code, action_label=action, sub_label=sub or "Période d'intervention",
                                  cells=[GridCell(cols=[i], kind="fill",
                                                  fill=next(c.fill for c in band["cols"][i]["cells"])) for i in active])
                    rows[("hdr",)] = row
                body_cells = [c for c in cells if id(c) not in header_ids and c.top >= band["bottom"] - 1
                              and c.bottom <= next_top + 1 and c.x0 >= gx0 - 2 and c.x1 <= gx1 + 2]
                for c in body_cells:      # croix éventuelles sous les mois
                    if c.text and _strip_accents(c.text.lower()) in MARKS:
                        code, action, sub = _row_labels(cells, gx0, c.top, c.bottom, fiche_code)
                        rows.setdefault((code, sub), GridRow(code=code, action_label=action, sub_label=sub)).cells.append(
                            GridCell(cols=_covered(cols, c.x0, c.x1), kind="mark", text=c.text))
            else:
                neutral = {f for f, _ in header_fills.items() if f}
                label_fills = Counter(c.fill for c in cells if c.x1 <= gx0 + 2 and c.fill)
                neutral |= {f for f, _ in label_fills.most_common(2)}
                body_cells = [c for c in cells if id(c) not in header_ids and c.top >= band["bottom"] - 1
                              and c.bottom <= next_top + 1 and c.x0 >= gx0 - 6]
                for c in sorted(body_cells, key=lambda c: (c.top, c.x0)):
                    covered = _covered(cols, c.x0, c.x1)
                    if not covered:
                        continue
                    kind = _classify_cell(c.text, len(covered), c.fill, neutral)
                    if not kind:
                        continue
                    code, action, sub = _row_labels(cells, gx0, c.top, c.bottom, fiche_code)
                    if not code and not sub:
                        continue
                    key = (code, sub)
                    rows.setdefault(key, GridRow(code=code, action_label=action, sub_label=sub)).cells.append(
                        GridCell(cols=covered, kind=kind, text=re.sub(r"\s+", " ", c.text), fill=c.fill))
            for row in rows.values():
                _dedupe_row(row)
            if rows:
                grids.append(Grid(id=f"p{number}_t{ti}_g{bi}", page=number, axis=axis, columns=cols,
                                  rows=list(rows.values()), n_year=n_year, source="geometry"))
    return grids


# ----------------------------------------------------------------- vision
def _vision_to_grids(v: VisionPageLLM, number: int, fiche_code: str | None) -> list[Grid]:
    out = []
    for gi, vg in enumerate(v.grids):
        cols, n_votes = [], Counter()
        for lab in vg.columns:
            p = parse_label(lab) or {}
            cols.append(GridColumn(label=lab, axis=p.get("axis", vg.axis), rel_start=p.get("rel_start"),
                                   rel_end=p.get("rel_end"), year_start=p.get("year_start"),
                                   year_end=p.get("year_end"), month=p.get("month")))
            if p.get("rel_start") is not None and p.get("year_start") is not None:
                n_votes[p["year_start"] - p["rel_start"]] += 1
        idx = {c.label.strip().lower(): i for i, c in enumerate(cols)}
        rows = []
        for vr in vg.rows:
            cells = []
            for vc in vr.cells:
                ci = sorted({idx[l.strip().lower()] for l in vc.columns if l.strip().lower() in idx})
                if ci:
                    cells.append(GridCell(cols=ci, kind=vc.kind, text=vc.text))
            if cells:
                code = re.sub(r"[\s-]", "", vr.code) if vr.code else fiche_code
                rows.append(GridRow(code=code, action_label=vr.action_label, sub_label=vr.sub_label, cells=cells))
        if rows and cols:
            out.append(Grid(id=f"p{number}_v{gi}", page=number, axis=vg.axis, columns=cols, rows=rows,
                            n_year=n_votes.most_common(1)[0][0] if n_votes else None, source="vision"))
    return out


def _active_keys(grid: Grid, row: GridRow) -> set:
    keys = set()
    for c in row.cells:
        for i in c.cols:
            col = grid.columns[i]
            keys.add(col.month if grid.axis == "month" else (col.rel_start, col.rel_end, col.year_start))
    return keys


def _reconcile(geo: list[Grid], vis: list[Grid]) -> tuple[list[Grid], list[str]]:
    """Accord géométrie/vision par ligne ; les lignes vues seulement en vision sont ajoutées."""
    notes = []
    used_v = set()
    for g in geo:
        cands = [v for v in vis if v.axis == g.axis]
        for row in g.rows:
            best, best_s, best_v = None, 0, None
            for v in cands:
                for vr in v.rows:
                    if (row.code or "") != (vr.code or "") and row.code and vr.code:
                        continue
                    s = fuzz.token_set_ratio(row.sub_label, vr.sub_label)
                    if s > best_s:
                        best, best_s, best_v = vr, s, v
            if best is not None and best_s >= 60:
                a, b = _active_keys(g, row), _active_keys(best_v, best)
                row.agreement = len(a & b) / max(1, len(a | b))
                used_v.add(id(best))
                if row.agreement < 1:
                    notes.append(f"{g.id} {row.code} « {row.sub_label} » : géométrie et vision divergent "
                                 f"(accord {row.agreement:.2f}).")
        g.source = "merged" if cands else g.source
    for v in vis:
        extra = [r for r in v.rows if id(r) not in used_v]
        if extra and not any(g.axis == v.axis for g in geo):
            v.rows = extra
            geo.append(v)
        elif extra:
            for r in extra:
                notes.append(f"Ligne vue uniquement par la vision p.{v.page} : {r.code} « {r.sub_label} ».")
    return geo, notes


def read_grids(doc: DocumentText, dm: DocumentMap, settings: Settings, llm: MistralClient, log=print) -> tuple[list[Grid], list[str]]:
    issues: list[str] = []
    grids: list[Grid] = []
    targets = [i for i in dm.pages if i.number in set(dm.pg_pages) and (i.grid_hint or "programmation" in i.roles)]
    pages = ", ".join(f"p.{i.number}" for i in targets) or "aucune"
    if settings.grid_vision_crosscheck:
        log(f"   {len(targets)} page(s) à lire en vision : {pages}")
    else:
        log(f"   {len(targets)} page(s) à lire (géométrie seule, vision désactivée) : {pages}")
    pdf_path = Path(doc.path)
    with pdfplumber.open(pdf_path) as pdf:
        for idx, info in enumerate(targets, 1):
            page = doc.page(info.number)
            geo = parse_page_geometry(pdf, info.number, info.fiche_code) if page.source == "native" else []
            vis: list[Grid] = []
            if settings.grid_vision_crosscheck:
                log(f"   {idx}/{len(targets)} vision p.{info.number}")
                try:
                    png = render_page_png(pdf_path, info.number, settings.render_dpi)
                    v = llm.vision_json(VisionPageLLM, prompts.SYSTEM_BASE, prompts.GRID_VISION, png,
                                        tag=f"grid_p{info.number}")
                    vis = _vision_to_grids(v, info.number, info.fiche_code) if v else []
                except LLMUnavailable as e:
                    if not geo:
                        issues.append(f"p.{info.number} : grille probable non lue (pas de géométrie, vision indisponible : {e}).")
            elif not geo:
                issues.append(f"p.{info.number} : grille probable non lue (pas de géométrie, vision désactivée).")
            merged, notes = _reconcile(geo, vis) if vis else (geo, [])
            issues += notes
            grids += merged
    return grids, issues


# ----------------------------------------------------------------- rendu texte
MONTH_NAMES = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def _col_txt(c: GridColumn) -> str:
    if c.axis == "month":
        return MONTH_NAMES[c.month or 0]
    if c.rel_start is not None:
        r = "N" if c.rel_start == 0 else f"N+{c.rel_start}"
        if c.rel_end != c.rel_start:
            r += f"→N+{c.rel_end}"
        return r
    return f"{c.year_start}" + (f"→{c.year_end}" if c.year_end != c.year_start else "")


def grids_as_text(grids: list[Grid]) -> str:
    if not grids:
        return "(aucune grille détectée)"
    out = []
    for g in grids:
        head = f"Grille {g.id} — page {g.page} — axe {g.axis}"
        if g.n_year is not None:
            head += f" — N = {g.n_year}"
        head += f" — colonnes : {', '.join(_col_txt(c) for c in g.columns)}"
        out.append(head)
        for r in g.rows:
            parts = []
            marks = [g.columns[i] for c in r.cells if c.kind in ("mark", "fill") for i in c.cols]
            if marks:
                lbl = "mois colorés" if g.axis == "month" else "cases cochées/colorées"
                parts.append(f"{lbl} : {', '.join(_col_txt(c) for c in marks)}")
            for c in r.cells:
                if c.kind == "band":
                    parts.append(f"bande {_col_txt(g.columns[c.cols[0]])}…{_col_txt(g.columns[c.cols[-1]])} : « {c.text} »")
            agr = f" [accord vision {r.agreement:.2f}]" if r.agreement is not None else ""
            out.append(f"  - {r.code or '?'} | {r.sub_label or r.action_label} | " + " ; ".join(parts) + agr)
    return "\n".join(out)
