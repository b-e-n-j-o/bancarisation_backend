"""Post-traitements des grilles d'une page : fusion des frises coupées, contexte de rattachement.

1. Fusion : une frise de 30 ans est souvent dessinée sur deux rangées (2023-2037 puis 2038-2052).
   Le module de lecture en voit deux grilles ; on les recolle en une seule quand elles sont
   empilées, de même largeur, avec le même nombre de lignes et des années qui se suivent.

2. Contexte : pour rattacher une grille à son action, on relève dans le PDF
   - `code_proche` / `titre_proche` : dernier titre d'action au-dessus (« SE1 : Suivi… »),
     sur la page ou, à défaut, sur les 2 pages précédentes ;
   - `ancre_avant` / `ancre_apres` : ligne de texte juste au-dessus et juste en dessous, qui
     servent au module 3 à insérer la grille au bon endroit du texte OCR ;
   - `texte_avant` : quelques lignes au-dessus (consigne, légende).
   Ce ne sont que des indices : le LLM d'extraction confirme le rattachement en lisant le texte.
"""
from __future__ import annotations

import copy
import re

from .geometrie import GeometriePage, texte_mots

# Code d'action ERC en tête de titre : « SE1 », « SE 1 », « TE-1 », « RG2 : … », « Fiche MG 1 »
RE_CODE = re.compile(r"^(?:fiche|mesure|action|code\s+mesure)?\s*[:\-–]?\s*([A-Z]{1,3})\s?[-–]?\s?(\d{1,2})\b(?!\s*(?:m²|ha|%|€))",
                     re.IGNORECASE)
MAX_MOTS_TITRE = 25
HAUT_PAGE, BAS_PAGE = 0.05, 0.92
CODES_EXCLUS = {"UG", "AT", "N", "P", "ZH", "PLU", "RD", "D", "A"}   # unités de gestion, cadastre, routes…


def lignes_repetees(textes_par_page: list[list[str]], part_min: float = 0.3) -> set[str]:
    """Lignes identiques présentes sur au moins 30 % des pages : en-têtes et pieds courants."""
    from collections import Counter
    compte = Counter(t for page in textes_par_page for t in set(page))
    seuil = max(3, part_min * len(textes_par_page))
    return {t for t, n in compte.items() if n >= seuil}


def _lignes_utiles(geo: GeometriePage, repetees: set[str] = frozenset()) -> list[tuple[float, float, str, list]]:
    """Lignes de texte hors en-têtes et pieds de page courants : (y0, y1, texte, mots)."""
    h = geo.rect.height
    out = []
    for l in geo.lignes_de_mots():
        y0, y1 = min(m.y0 for m in l), max(m.y1 for m in l)
        texte = " ".join(m.texte for m in l)
        if texte in repetees or y1 < geo.rect.y0 + HAUT_PAGE * h or y0 > geo.rect.y0 + BAS_PAGE * h:
            continue
        out.append((y0, y1, texte, l))
    return out


def _dans_cellule_etroite(geo: GeometriePage, mot) -> bool:
    """Le mot est-il dans une cellule de tableau étroite (colonne « Code », « UG »…) plutôt que dans
    un titre ? Un titre de fiche est soit hors tableau, soit dans un bandeau large."""
    c = geo.cellule_englobante(mot.cx, mot.cy)
    return c.bordee["g"] and c.bordee["d"] and (c.x1 - c.x0) < 0.45 * geo.rect.width


def titres_de_page(geo: GeometriePage, repetees: set[str] = frozenset()) -> list[dict]:
    """Lignes qui ressemblent à un titre d'action : code en tête, ligne courte, hors cellule étroite."""
    titres = []
    for y0, y1, texte, mots in _lignes_utiles(geo, repetees):
        if len(mots) > MAX_MOTS_TITRE or _dans_cellule_etroite(geo, mots[0]):
            continue
        m = RE_CODE.match(texte.strip())
        if m and m.group(1).isupper() and m.group(1).upper() not in CODES_EXCLUS:
            titres.append({"y0": round(y0, 1), "texte": texte.strip(), "code": f"{m.group(1).upper()}{m.group(2)}"})
    return titres


def ajouter_contexte(geo: GeometriePage, grilles: list[dict], titres_precedents: list[dict],
                     repetees: set[str] = frozenset()) -> None:
    lignes = _lignes_utiles(geo, repetees)
    titres = titres_de_page(geo, repetees)
    for g in grilles:
        x0, y0, x1, y1 = g["bbox"]
        dans_grille = lambda ly0, ly1: ly1 > y0 - 1 and ly0 < y1 + 1
        avant = [l for l in lignes if l[1] <= y0 + 1 and not dans_grille(l[0], l[1])]
        apres = [l for l in lignes if l[0] >= y1 - 1 and not dans_grille(l[0], l[1])]
        au_dessus = [t for t in titres if t["y0"] < y0]
        if au_dessus:
            titre = dict(au_dessus[-1], page=g["page"])
        else:
            titre = titres_precedents[-1] if titres_precedents else None
        g["contexte"] = {
            "code_proche": titre["code"] if titre else None,
            "titre_proche": titre["texte"] if titre else None,
            "titre_page": titre.get("page") if titre else None,
            "ancre_avant": avant[-1][2] if avant else None,
            "ancre_apres": apres[0][2] if apres else None,
            "texte_avant": " / ".join(l[2] for l in avant[-3:]) if avant else None,
        }


# ---------------------------------------------------------------------------- fusion
def _fin(col: dict):
    return col.get("annee_fin"), col.get("n_fin")


def _debut(col: dict):
    return col.get("annee_debut"), col.get("n_debut")


def _se_suivent(a: dict, b: dict) -> bool:
    (af, nf), (bd, nd) = _fin(a["colonnes"][-1]), _debut(b["colonnes"][0])
    if af is not None and bd is not None:
        return bd == af + 1
    if nf is not None and nd is not None:
        return nd == nf + 1
    return False


def _compatibles(la: dict, lb: dict) -> bool:
    return not la["libelle"] or not lb["libelle"] or la["libelle"] == lb["libelle"]


def fusionner_frises(grilles: list[dict]) -> list[dict]:
    """Recolle les frises annuelles coupées en plusieurs rangées sur une même page."""
    grilles = sorted(grilles, key=lambda g: g["bbox"][1])
    sortie: list[dict] = []
    for g in grilles:
        prec = sortie[-1] if sortie else None
        if prec and prec["type_axe"] == g["type_axe"] == "annees" and _se_suivent(prec, g):
            ecart = g["bbox"][1] - prec["bbox"][3]
            za, zb = prec.get("zone_origine", prec["zone_temporelle"]), g["zone_temporelle"]
            recouvre = min(za[1], zb[1]) - max(za[0], zb[0]) >= 0.7 * min(za[1] - za[0], zb[1] - zb[0])
            corps_a = [l for l in prec["lignes"] if l["role"] == "corps"]
            corps_b = [l for l in g["lignes"] if l["role"] == "corps"]
            if -2 <= ecart <= 120 and recouvre and len(corps_a) == len(corps_b) \
                    and all(_compatibles(a, b) for a, b in zip(corps_a, corps_b)):
                sortie[-1] = _fusionner(prec, g)
                continue
        sortie.append(g)
    for k, g in enumerate(sortie):
        g["id"] = f"p{g['page']:03d}_g{k + 1}"
    return sortie


def _decaler(marques: list[dict], decalage: int, ligne: dict, colonnes_b: list[dict]) -> list[dict]:
    out = []
    for m in marques:
        m = copy.deepcopy(m)
        m["colonnes"] = [i + decalage for i in m["colonnes"]]
        m.setdefault("y0", ligne["y0"])
        m.setdefault("y1", ligne["y1"])
        out.append(m)
    return out


def _fusionner(a: dict, b: dict) -> dict:
    g = copy.deepcopy(a)
    n = len(a["colonnes"])
    rang_b = len(a.get("rangees", [a["bbox"]]))
    for c in b["colonnes"]:
        c = dict(c)
        c["i"] = c["i"] + n
        c["rangee"] = rang_b + c.get("rangee", 0)
        g["colonnes"].append(c)
    for l in g["lignes"]:                                  # positions explicites pour les overlays
        for m in l["marques"]:
            m.setdefault("y0", l["y0"])
            m.setdefault("y1", l["y1"])
    corps_g = [l for l in g["lignes"] if l["role"] == "corps"]
    corps_b = [l for l in b["lignes"] if l["role"] == "corps"]
    for la, lb in zip(corps_g, corps_b):
        la["libelle"] = la["libelle"] or lb["libelle"]
        la["marques"] += _decaler(lb["marques"], n, lb, b["colonnes"])
        la["segments"] = la.get("segments", []) + [
            dict(s, colonnes=[i + n for i in s["colonnes"]]) for s in lb.get("segments", [])]
    for lb in b["lignes"]:
        if lb["role"] == "entete":
            g["lignes"].append(dict(lb, marques=_decaler(lb["marques"], n, lb, b["colonnes"])))
    g["bbox"] = [min(a["bbox"][0], b["bbox"][0]), a["bbox"][1], max(a["bbox"][2], b["bbox"][2]), b["bbox"][3]]
    g["zone_origine"] = a.get("zone_origine", a["zone_temporelle"])
    g["fusion_de"] = a.get("fusion_de", [a["id"]]) + [b["id"]]
    g["rangees"] = a.get("rangees", [a["bbox"]]) + [b["bbox"]]
    g["confiance"] = "haute" if a["confiance"] == b["confiance"] == "haute" else "moyenne"
    return g
