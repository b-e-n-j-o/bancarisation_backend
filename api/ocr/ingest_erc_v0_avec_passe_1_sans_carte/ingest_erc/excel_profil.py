"""Profileur Excel déterministe.

Couche 1 — grille normalisée : fusions dépliées, type, couleur, formule.
Couche 2 — blocs : en-têtes temporels (années + rangs N/N+x), colonnes
           identifiantes, titre, lignes de données, lignes de totaux, légendes.
Couche 3 — enregistrements : une ligne par cellule temporelle non vide,
           rattachée au groupe de lignes couvert par la fusion.
Plus : détection des tableaux de synthèse code ↔ UG (utilisés par la passe 1).

Rien d'ici n'est spécifique à l'ITK Le Barp : seuls des motifs génériques
(années, N+x, codes d'action, UG, colonnes cadastrales) sont utilisés.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import openpyxl

from .cadastre import cle_parcelle
from .inventaire import norm

RE_CODE = re.compile(r"^\s*([A-Z]{1,4})\s?-?\s?(\d{1,2}[a-z]?)\s*$")
RE_UG_TOKEN = re.compile(r"\bUG\s?-?\s?(\d{1,3}[a-z]?)\b", re.I)
RE_RANG = re.compile(r"^\s*(?:N\s*(?:\+\s*(\d+))?|ann[ée]e\s*N|[ée]tat\s*z[ée]ro)\s*$", re.I)


def code_norm(v) -> Optional[str]:
    m = RE_CODE.match(str(v or ""))
    return f"{m.group(1)}{m.group(2)}" if m else None


def ugs_dans(v) -> list[str]:
    s = str(v or "")
    ugs = [f"UG{x.upper()}" for x in RE_UG_TOKEN.findall(s)]
    # "UG 2, UG 4 et 6" : nombres orphelins après une première UG
    if ugs:
        queue = s[RE_UG_TOKEN.search(s).end():]
        for n in re.findall(r"(?:,|\bet\b)\s*(\d{1,3})\b", queue):
            if f"UG{n}" not in ugs:
                ugs.append(f"UG{n}")
    return ugs


def _annee(v) -> Optional[int]:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() and 1950 <= f <= 2100 else None


def _rang(v) -> Optional[int]:
    """'Etat zéro' -> -1 (convention), 'N' -> 0, 'N+5' -> 5"""
    s = str(v or "").strip()
    m = RE_RANG.match(s)
    if not m:
        return None
    if re.search(r"z[ée]ro", s, re.I):
        return -1
    return int(m.group(1)) if m.group(1) else 0


# ------------------------------------------------------------------ couche 1
@dataclass
class C:
    v: Any
    typ: str                 # "txt" | "num" | "vide"
    ancre: tuple             # (row, col) de la cellule porteuse
    span: tuple              # (r1, c1, r2, c2)
    fill: Optional[str] = None
    formule: Optional[str] = None


@dataclass
class Feuille:
    nom: str
    nrow: int
    ncol: int
    g: dict = field(default_factory=dict)   # (r, c) -> C

    def get(self, r, c) -> C:
        return self.g.get((r, c), C(None, "vide", (r, c), (r, c, r, c)))

    def ligne(self, r):
        return [self.get(r, c) for c in range(1, self.ncol + 1)]


def _typ(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return "vide"
    return "num" if isinstance(v, (int, float)) else "txt"


def lire_grille(chemin: str) -> list[Feuille]:
    wb_v = openpyxl.load_workbook(chemin, data_only=True)
    wb_f = openpyxl.load_workbook(chemin, data_only=False)
    feuilles = []
    for ws in wb_v.worksheets:
        wf = wb_f[ws.title]
        f = Feuille(ws.title, ws.max_row, ws.max_column)
        spans = {}
        for mr in ws.merged_cells.ranges:
            for r in range(mr.min_row, mr.max_row + 1):
                for c in range(mr.min_col, mr.max_col + 1):
                    spans[(r, c)] = (mr.min_row, mr.min_col, mr.max_row, mr.max_col)
        for row in ws.iter_rows():
            for cell in row:
                r, c = cell.row, cell.column
                sp = spans.get((r, c), (r, c, r, c))
                src = ws.cell(sp[0], sp[1])
                v = src.value
                if isinstance(v, str):
                    v = v.strip() or None
                fill = None
                if src.fill is not None and src.fill.fill_type == "solid":
                    rgb = src.fill.fgColor.rgb
                    fill = rgb if isinstance(rgb, str) and rgb not in ("00000000",) else None
                fv = wf.cell(sp[0], sp[1]).value
                formule = fv if isinstance(fv, str) and fv.startswith("=") else None
                if v is None and not fill:
                    continue
                f.g[(r, c)] = C(v, _typ(v), (sp[0], sp[1]), sp, fill, formule)
        feuilles.append(f)
    return feuilles


# ------------------------------------------------------------------ couche 2
LIBELLES_COL = [
    ("commune", r"^commune$"), ("prefixe", r"^pr[ée]fixe$"), ("section", r"^section$"),
    ("code", r"code\s*mesure|n(°|um[ée]ro)\s*de\s*la\s*mesure|^code$"),
    ("numero", r"^num[ée]ro$"), ("subdiv", r"subdivision|^subdiv"),
    ("surface", r"surface"),
    ("ug", r"unit[ée]s?\s*de\s*gestion|^ug$"), ("ref", r"r[ée]f[ée]rence\s*cadastrale"),
]


@dataclass
class Bloc:
    id: str
    feuille: str
    titre: str
    ligne_entete: int
    ligne_rang: Optional[int]
    lignes: list[int]
    cols_id: dict                 # nom -> col
    cols_temps: dict              # col -> {"annee": int, "rang": int|None}
    ancre_etat_zero: Optional[int] = None
    ancre_N: Optional[int] = None
    nature: str = "?"             # "planning" | "couts" | "mixte"
    parcelles: dict = field(default_factory=dict)   # row -> cle
    codes: dict = field(default_factory=dict)       # row -> code
    totaux: list = field(default_factory=list)      # [{"ligne": r, "valeurs": {annee: v}}]
    suite_de: Optional[str] = None


def _entetes_temps(f: Feuille, r: int) -> dict:
    cols = {}
    for c in range(1, f.ncol + 1):
        cel = f.get(r, c)
        if cel.ancre != (r, c):
            continue
        a = _annee(cel.v)
        if a:
            cols[c] = a
    # garder la plus longue suite d'années consécutives
    if len(cols) < 5:
        return {}
    items = sorted(cols.items())
    best, cur = [], [items[0]]
    for prev, it in zip(items, items[1:]):
        if it[1] == prev[1] + 1:
            cur.append(it)
        else:
            best, cur = max(best, cur, key=len), [it]
    best = max(best, cur, key=len)
    return dict(best) if len(best) >= 5 else {}


def _libelle_col(txt) -> Optional[str]:
    t = norm(str(txt or ""))
    for nom, motif in LIBELLES_COL:
        if re.search(motif, t):
            return nom
    return None


def _est_ligne_donnees(f: Feuille, r: int, cols_id: dict) -> bool:
    if "numero" in cols_id:
        num = str(f.get(r, cols_id["numero"]).v or "").strip()
        sec = str(f.get(r, cols_id.get("section", 0)).v or "").strip()
        return bool(re.fullmatch(r"\d{1,4}(\.0)?", num)) and bool(re.fullmatch(r"[A-Za-z0-9]{1,2}", sec))
    if "code" in cols_id:
        return code_norm(f.get(r, cols_id["code"]).v) is not None
    return False


def detecter_blocs(f: Feuille) -> list[Bloc]:
    blocs: list[Bloc] = []
    r = 1
    while r <= f.nrow:
        temps = _entetes_temps(f, r)
        if not temps:
            r += 1
            continue
        c0 = min(temps)
        # colonnes identifiantes : libellés dans la ligne d'en-tête et la suivante
        cols_id = {}
        for rr in (r, r + 1):
            for c in range(1, c0):
                nom = _libelle_col(f.get(rr, c).v)
                if nom and nom not in cols_id:
                    cols_id[nom] = c
        # ligne de rangs (N, N+x) : dans les 2 lignes suivantes
        ligne_rang, rangs = None, {}
        for rr in (r + 1, r + 2):
            rg = {c: _rang(f.get(rr, c).v) for c in temps}
            if sum(v is not None for v in rg.values()) >= len(temps) // 2:
                ligne_rang, rangs = rr, rg
                break
        debut = (ligne_rang or r) + 1
        lignes, rr = [], debut
        while rr <= f.nrow and _est_ligne_donnees(f, rr, cols_id) and not _entetes_temps(f, rr):
            lignes.append(rr)
            rr += 1
        # titre : première cellule texte au-dessus, sans traverser une ligne de données
        titre, suite = None, None
        for up in range(r - 1, max(0, r - 4), -1):
            if _est_ligne_donnees(f, up, cols_id):
                break
            txts = [x.v for x in f.ligne(up) if x.typ == "txt" and x.ancre[0] == up]
            if txts:
                titre = str(txts[0])
                break
        if titre is None and blocs and blocs[-1].feuille == f.nom and blocs[-1].lignes \
                and blocs[-1].lignes[-1] >= r - 3:
            titre, suite = blocs[-1].titre, blocs[-1].id
        b = Bloc(id=f"{f.nom}#{len(blocs) + 1}", feuille=f.nom, titre=titre or "(sans titre)",
                 ligne_entete=r, ligne_rang=ligne_rang, lignes=lignes, cols_id=cols_id,
                 cols_temps={c: {"annee": a, "rang": rangs.get(c)} for c, a in temps.items()},
                 suite_de=suite)
        for c, t in b.cols_temps.items():
            if t["rang"] == -1 and b.ancre_etat_zero is None:
                b.ancre_etat_zero = t["annee"]
            if t["rang"] is not None and t["rang"] >= 0 and b.ancre_N is None:
                b.ancre_N = t["annee"] - t["rang"]
        for lr in lignes:
            g = lambda k: f.get(lr, cols_id[k]).v if k in cols_id else None
            if "numero" in cols_id:
                b.parcelles[lr] = cle_parcelle(g("section"), g("numero"), g("subdiv"))
            if "code" in cols_id:
                b.codes[lr] = code_norm(g("code"))
        # nature
        n_num = n_txt = 0
        for lr in lignes:
            for c in temps:
                cel = f.get(lr, c)
                if cel.ancre == (lr, c) or cel.ancre[0] == lr:
                    n_num += cel.typ == "num"
                    n_txt += cel.typ == "txt"
        b.nature = "couts" if n_num > n_txt else ("planning" if n_num == 0 else "mixte")
        # lignes de totaux : juste après le bloc, numériques sous les années, sans identifiant
        for tr in range((lignes[-1] if lignes else debut) + 1, min(f.nrow, (lignes[-1] if lignes else debut) + 4) + 1):
            vals = {t["annee"]: f.get(tr, c).v for c, t in b.cols_temps.items()
                    if f.get(tr, c).typ == "num" and f.get(tr, c).ancre == (tr, c)}
            if len(vals) >= 3:
                b.totaux.append({"ligne": tr, "valeurs": vals})
        blocs.append(b)
        r = (lignes[-1] if lignes else r) + 1
    return blocs


def detecter_legendes(f: Feuille) -> dict:
    """Paires « libellé long | abréviation » (ex. Coupe rase | CR)."""
    leg = {}
    for r in range(1, f.nrow + 1):
        cels = [x for x in f.ligne(r) if x.typ == "txt" and x.ancre[0] == r]
        uniq = []
        for x in cels:
            if not uniq or uniq[-1].ancre != x.ancre:
                uniq.append(x)
        if len(uniq) == 2 and re.fullmatch(r"[A-Z]{1,4}", str(uniq[1].v)) \
                and len(str(uniq[0].v)) > 4:
            leg[str(uniq[1].v)] = str(uniq[0].v)
    return leg


def coefficient_totaux(f: Feuille, blocs: list[Bloc]) -> dict:
    """Cherche un multiplicateur k constant entre les lignes de totaux et les montants
    du tableur (valeurs unitaires, sommes de colonne d'un bloc, sommes de 2 blocs).
    Un k = 1,1 / 1,2 / 1,055 dominant trahit une TVA implicite."""
    couts = [b for b in blocs if b.nature in ("couts", "mixte")]
    par_col = {}
    for b in couts:
        for c in b.cols_temps:
            vals = [f.get(lr, c).v for lr in b.lignes
                    if f.get(lr, c).typ == "num" and f.get(lr, c).ancre == (lr, c)]
            if vals:
                par_col[(b.id, c)] = vals
    bases = set()
    for vals in par_col.values():
        bases.update(vals)
        bases.add(sum(vals))
    cles = list(par_col)
    for i, k1 in enumerate(cles):
        for k2 in cles[i + 1:]:
            if k1[1] == k2[1]:
                bases.add(sum(par_col[k1]) + sum(par_col[k2]))
    totaux = [v for b in couts for t in b.totaux for v in t["valeurs"].values() if v]
    scores = {}
    for k in (1.0, 1.055, 1.1, 1.2):
        scores[k] = sum(any(abs(v / k - x) < 0.02 for x in bases) for v in totaux)
    best = max(scores, key=scores.get) if totaux else None
    return {"nb_totaux": len(totaux), "scores": scores, "k": best,
            "part_expliquee": round(scores[best] / len(totaux), 2) if totaux else 0}


# ------------------------------------------------------------------ synthèse
@dataclass
class LigneSynthese:
    feuille: str
    ligne: int
    code: str
    ugs: list
    champs: dict
    cellule_ug: str


def detecter_syntheses(f: Feuille) -> tuple[list[LigneSynthese], dict]:
    """Tableau à en-tête « code/numéro de mesure » + « unité de gestion »."""
    lignes, ug_libelles = [], {}
    for r in range(1, f.nrow + 1):
        entetes = {c: f.get(r, c).v for c in range(1, f.ncol + 1) if f.get(r, c).typ == "txt"}
        noms = {c: _libelle_col(v) for c, v in entetes.items()}
        c_code = next((c for c, n in noms.items() if n == "code"), None)
        c_ug = next((c for c, n in noms.items() if n == "ug"), None)
        if not (c_code and c_ug):
            continue
        for rr in range(r + 1, f.nrow + 1):
            code = code_norm(f.get(rr, c_code).v)
            if not code:
                if all(x.typ == "vide" for x in f.ligne(rr)):
                    break
                continue
            champs = {}
            for c, lib in entetes.items():
                if c in (c_code, c_ug):
                    continue
                v = f.get(rr, c).v
                if v is not None:
                    champs[norm(str(lib))[:40]] = str(v)
            cel = f.get(rr, c_ug)
            lignes.append(LigneSynthese(f.nom, rr, code, ugs_dans(cel.v), champs,
                                        f"{openpyxl.utils.get_column_letter(c_ug)}{rr}"
                                        + (" (fusion)" if cel.span[0] != cel.span[2] else "")))
        break
    # légende des UG : « UG n | libellé »
    for r in range(1, f.nrow + 1):
        cels = [x for x in f.ligne(r) if x.typ == "txt" and x.ancre[0] == r]
        for a, b in zip(cels, cels[1:]):
            if re.fullmatch(r"UG\s?\d{1,3}[a-z]?", str(a.v).strip(), re.I) and not ugs_dans(b.v):
                ug_libelles[f"UG{re.sub(r'[^0-9a-z]', '', str(a.v).lower().replace('ug', ''))}"] = \
                    (str(b.v), f"{f.nom}!{openpyxl.utils.get_column_letter(b.ancre[1])}{r}")
    return lignes, ug_libelles


def profiler_excel(chemin: str) -> dict:
    feuilles = lire_grille(chemin)
    out = {"feuilles": [], "blocs": [], "syntheses": [], "ug_libelles": {}, "legendes": {},
           "totaux": []}
    for f in feuilles:
        blocs = detecter_blocs(f)
        syn, ugl = detecter_syntheses(f)
        leg = detecter_legendes(f)
        out["feuilles"].append({"nom": f.nom, "dimensions": (f.nrow, f.ncol),
                                "nb_cellules": len(f.g), "nb_blocs": len(blocs),
                                "synthese": bool(syn)})
        out["blocs"] += [(f, b) for b in blocs]
        out["syntheses"] += syn
        out["ug_libelles"].update(ugl)
        out["legendes"].update(leg)
        if blocs:
            out["totaux"].append({"feuille": f.nom, **coefficient_totaux(f, blocs)})
    return out
