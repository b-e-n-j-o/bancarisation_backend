"""Pont pdfvisuel (modules 1 et 3) vers ingest_erc.

Le module 2 (vision API) n'est pas lancé : seules les grilles lues dans le dessin
du PDF sont réinjectées. Le profil est mis en cache par sha256 du fichier.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from ..passes.extract_referentiel import _f
from .mdnorm import markdown_vers_texte
from ..modeles import PageTexte

_ANALYSE = Path(__file__).resolve().parents[2] / "analyse_pdf"
RE_CODE = re.compile(r"\b([A-Z]{1,4})\s*[-–]?\s*0*(\d{1,2})\b")
RE_PAGE = re.compile(r"=== page (\d+) ===\n(.*?)(?=\n=== page \d+ ===|\Z)", re.S)

MARQUES_ANNEE = {"croix", "remplissage", "remplissage_minoritaire"}


def _pdfvisuel():
    if str(_ANALYSE) not in sys.path:
        sys.path.insert(0, str(_ANALYSE))
    from pdfvisuel.m1_detection.profil import analyser_pdf
    from pdfvisuel.m3_assemblage.assemblage import assembler
    return analyser_pdf, assembler


def chemin_profil(sha: str, cache_dir: Path) -> Path:
    return Path(cache_dir) / f"{sha}.pdfvisuel.json"


def charger_ou_analyser(doc, cache_dir: Path | None) -> dict | None:
    """Profil module 1, relu depuis `{sha}.pdfvisuel.json` s'il existe."""
    if doc.role != "plan_gestion" or not str(doc.chemin).lower().endswith(".pdf"):
        return None
    cache = chemin_profil(doc.sha256, cache_dir) if cache_dir else None
    if cache and cache.exists():
        return json.loads(cache.read_text())
    analyser_pdf, _ = _pdfvisuel()
    profil = analyser_pdf(doc.chemin)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(profil, ensure_ascii=False))
    return profil


def faits_ancres_plans(docs, cache_dir: Path | None) -> list:
    """Un fait d'ancre par document et par valeur de N (colonnes simples seulement)."""
    out = []
    for d in docs:
        if d.role != "plan_gestion":
            continue
        print(f"   → pdfvisuel (grilles, ancre N) : {d.nom}", flush=True)
        try:
            profil = charger_ou_analyser(d, cache_dir)
        except Exception as err:  # noqa: BLE001
            print(f"   ⚠️  pdfvisuel ignoré ({d.nom}) : {err}", flush=True)
            continue
        if profil:
            out += faits_ancre(d, profil)
    return out


def _n_colonne_simple(col: dict) -> int | None:
    """N = année − rang, uniquement pour une colonne d'une seule année."""
    if col.get("plage"):
        return None
    n0, a0 = col.get("n_debut"), col.get("annee_debut")
    if n0 is None or a0 is None:
        return None
    if col.get("n_fin") not in (None, n0) or col.get("annee_fin") not in (None, a0):
        return None
    return int(a0) - int(n0)


def faits_ancre(doc, profil: dict) -> list:
    par_n: dict[int, list[str]] = {}
    pages: dict[int, int] = {}
    for page in profil.get("pages") or []:
        for g in page.get("grilles") or []:
            if g.get("type_axe") != "annees":
                continue
            ns = [n for n in (_n_colonne_simple(c) for c in g.get("colonnes") or []) if n is not None]
            if not ns:
                continue
            n, k = Counter(ns).most_common(1)[0]
            if k < len(ns) and Counter(ns).most_common(2)[-1][1] == k:
                for n2 in set(ns):
                    par_n.setdefault(n2, []).append(g["id"])
                    pages.setdefault(n2, g["page"])
                continue
            par_n.setdefault(n, []).append(g["id"])
            pages.setdefault(n, g["page"])
    out = []
    for n, ids in sorted(par_n.items()):
        out.append(_f(
            "ancre_temporelle", "projet",
            {"etat_zero": None, "N": n, "bloc": ids[0], "grilles": ids, "nature": "planning_pdf"},
            doc.nom, f"p.{pages[n]} grille {ids[0]}",
            f"N = {n} ({len(ids)} grille(s), colonnes année − rang)",
        ))
    return out


def _codes_ligne(libelle: str) -> list[str]:
    vus = []
    for m in RE_CODE.finditer(libelle or ""):
        code = f"{m.group(1).upper()}{int(m.group(2))}"
        if code not in vus:
            vus.append(code)
    return vus


def _col_simple(col: dict) -> bool:
    return _n_colonne_simple(col) is not None or (
        not col.get("plage") and col.get("n_debut") is not None
        and col.get("n_fin") in (None, col.get("n_debut"))
        and col.get("annee_debut") is None
    )


def lectures_plannings(profil: dict) -> list[dict]:
    """Lignes de grilles annuelles : rangs des croix et cellules colorées, pas les bandes."""
    out = []
    for page in profil.get("pages") or []:
        for g in page.get("grilles") or []:
            if g.get("type_axe") != "annees":
                continue
            cols = g.get("colonnes") or []
            ctx = g.get("contexte") or {}
            for ligne in g.get("lignes") or []:
                if ligne.get("role") == "entete":
                    continue
                lib = ligne.get("libelle") or ""
                codes = _codes_ligne(lib) or ([ctx["code_proche"]] if ctx.get("code_proche") else [])
                if not codes:
                    continue
                rangs, avert = [], []
                for m in ligne.get("marques") or []:
                    if m.get("type") == "bande":
                        avert.append("bande non interprétée : la règle reste à la fiche")
                        continue
                    if m.get("type") not in MARQUES_ANNEE:
                        continue
                    for i in m.get("colonnes") or []:
                        if i >= len(cols):
                            continue
                        col = cols[i]
                        if col.get("plage") or not _col_simple(col):
                            avert.append(f"colonne à plage ignorée ({col.get('libelle')})")
                            continue
                        if col.get("n_debut") is not None:
                            rangs.append(int(col["n_debut"]))
                if not rangs:
                    continue
                for code in codes:
                    out.append({
                        "code": code, "rangs": sorted(set(rangs)), "libelle": lib[:160],
                        "page": g["page"], "grille": g["id"],
                        "avertissements": sorted(set(avert)),
                    })
    return out


def lectures_fenetres(profil: dict) -> list[dict]:
    """Grilles mensuelles : plages de mois colorés, rattachées au code proche."""
    out = []
    for page in profil.get("pages") or []:
        for g in page.get("grilles") or []:
            if g.get("type_axe") != "mois":
                continue
            code = (g.get("contexte") or {}).get("code_proche")
            if not code:
                continue
            cols = g.get("colonnes") or []
            mois = set()
            for ligne in g.get("lignes") or []:
                for m in ligne.get("marques") or []:
                    if m.get("type") not in MARQUES_ANNEE:
                        continue
                    for i in m.get("colonnes") or []:
                        if i < len(cols) and cols[i].get("mois"):
                            mois.add(int(cols[i]["mois"]))
            plages = _plages(sorted(mois))
            if plages:
                out.append({"code": code, "plages": plages, "page": g["page"], "grille": g["id"]})
    return out


def _plages(mois: list[int]) -> list[tuple[int, int]]:
    if not mois:
        return []
    plages, d = [], mois[0]
    for a, b in zip(mois, mois[1:] + [None]):
        if b is None or b != a + 1:
            plages.append((d, a))
            d = b
    return plages


def pages_enrichies(doc, cache_dir: Path | None) -> list[PageTexte]:
    """Copie des pages dont le texte est le markdown OCR enrichi par le module 3.

    Le document d'origine n'est pas modifié : la passe 1 continue de lire le markdown brut.
    """
    try:
        profil = charger_ou_analyser(doc, cache_dir)
        if not profil or not cache_dir:
            return doc.pages
        _, assembler = _pdfvisuel()
        profil_path = chemin_profil(doc.sha256, cache_dir)
        if not profil_path.exists():
            profil_path.write_text(json.dumps(profil, ensure_ascii=False))
        ocr_path = profil_path.with_suffix(".ocr.json")
        ocr_path.write_text(json.dumps([p.markdown or "" for p in doc.pages], ensure_ascii=False))
        assembler(profil_path, None, ocr_path, "remplacer")
        enrichi = (profil_path.parent / "document_enrichi.md").read_text()
    except (Exception, SystemExit) as err:  # noqa: BLE001
        print(f"   ⚠️  texte enrichi ignoré ({doc.nom}) : {err}", flush=True)
        return doc.pages
    blocs = {int(n): corps for n, corps in RE_PAGE.findall(enrichi)}
    out = []
    for p in doc.pages:
        corps = blocs.get(p.num)
        if corps is None:
            out.append(p)
            continue
        out.append(p.model_copy(update={"texte": markdown_vers_texte(corps)}))
    return out
