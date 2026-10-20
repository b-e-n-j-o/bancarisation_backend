"""Normalisation de la sortie Mistral OCR (markdown) avant les extracteurs.

Trois usages :
  1. `markdown_vers_texte(md)` : texte « plat » comparable à pdftotext
     (images, emphases, titres #, puces, LaTeX, tableaux aplatis) pour les regex ;
  2. `tableaux(md)` : tableaux markdown -> lignes de cellules, avec remplissage
     vers le bas des cellules vides (cellules fusionnées perdues par l'OCR) ;
  3. `retirer_entetes(pages)` + `fiabilite_nombres(md, texte_natif)` : bruit
     répété (en-têtes / pieds) et contrôle des chiffres OCR contre la couche texte.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

RE_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
RE_SUP = re.compile(r"\$\^\{?([^}$]*)\}?\$")
RE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
RE_PUCE = re.compile(r"^\s*(?:[-*•○◦▪]\s+)+")
RE_TITRE = re.compile(r"^\s{0,3}#{1,6}\s+")


def _emphases(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
    return s.replace("**", "")


def cellules(ligne: str) -> list[str]:
    corps = ligne.strip()
    if corps.startswith("|"):
        corps = corps[1:]
    if corps.endswith("|"):
        corps = corps[:-1]
    return [_emphases(c).strip() for c in corps.split("|")]


def est_ligne_tableau(ligne: str) -> bool:
    return ligne.strip().startswith("|") and ligne.count("|") >= 2


def markdown_vers_texte(md: str) -> str:
    if not md:
        return md
    out = []
    for l in RE_COMMENT.sub("", md).splitlines():
        if RE_SEP.match(l):
            continue
        l = RE_IMG.sub("", l)
        l = RE_SUP.sub(r"\1", l)
        if est_ligne_tableau(l):
            l = "    ".join(c for c in cellules(l) if c)
        else:
            titre = bool(RE_TITRE.match(l))
            l = RE_TITRE.sub("", l)
            l = RE_PUCE.sub("", l)
            l = _emphases(l)
            if titre:
                l = "# " + l.strip()
        out.append(l.rstrip())
    txt = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return txt.strip("\n")


@dataclass
class TableauMD:
    page: int
    index: int
    entete: list[str]
    lignes: list[list[str]]
    remplies: list[set] = field(default_factory=list)
    titre: str = ""


def tableaux(md: str, page: int = 0, remplir: bool = False) -> list[TableauMD]:
    res, bloc, avant = [], [], []
    lignes = RE_COMMENT.sub("", md).splitlines()

    def fermer():
        if len(bloc) < 2:
            return
        rows = [cellules(l) for l in bloc if not RE_SEP.match(l)]
        if not rows:
            return
        titre = next((re.sub(r"[*#]", "", a).strip() for a in reversed(avant[-4:])
                      if a.strip() and not RE_IMG.fullmatch(a.strip())), "")
        t = TableauMD(page, len(res), rows[0], rows[1:], titre=titre)
        if remplir:
            _remplir_vers_le_bas(t)
        res.append(t)

    for l in lignes:
        if est_ligne_tableau(l):
            bloc.append(l)
        else:
            if bloc:
                fermer()
                bloc = []
            avant.append(l)
    if bloc:
        fermer()
    return res


def _remplir_vers_le_bas(t: TableauMD, colonnes: set | None = None) -> None:
    """Une cellule vide sous une valeur, dans une ligne qui a par ailleurs du contenu,
    est traitée comme une cellule fusionnée verticalement (perdue à l'OCR).
    À n'appliquer qu'aux colonnes où c'est plausible (jamais « subdivision »)."""
    n = max([len(t.entete)] + [len(r) for r in t.lignes] or [0])
    dernier = [""] * n
    remplies = set()
    for i, r in enumerate(t.lignes):
        r.extend([""] * (n - len(r)))
        if sum(bool(c) for c in r) == 0:
            continue
        for j in range(n):
            if colonnes is not None and j not in colonnes:
                continue
            if r[j]:
                dernier[j] = r[j]
            elif dernier[j]:
                r[j] = dernier[j]
                remplies.add((i, j))
    t.remplies = [remplies]


remplir_vers_le_bas = _remplir_vers_le_bas


def retirer_entetes(textes: list[str], seuil: float = 0.5) -> list[str]:
    """Supprime les lignes présentes sur plus de `seuil` des pages (en-têtes, pieds)
    et les numéros de page isolés."""
    def cle(l: str) -> str:
        return re.sub(r"\s+\d{1,3}\s*$", "", l.strip())

    compte: Counter = Counter()
    for t in textes:
        compte.update({cle(l) for l in t.splitlines() if len(l.strip()) > 8})
    n = max(1, len(textes))
    bruit = {l for l, c in compte.items() if c / n >= seuil}
    out = []
    for t in textes:
        garde = [l for l in t.splitlines()
                 if cle(l) not in bruit and not re.fullmatch(r"\s*\d{1,3}(\s*/\s*\d{1,3})?\s*", l)]
        out.append("\n".join(garde))
    retirer_entetes.dernier_bruit = sorted(bruit, key=lambda l: -compte[l])
    return out


retirer_entetes.dernier_bruit = []


RE_NOMBRE = re.compile(r"\d{1,3}(?:[ \u202f\u00a0]\d{3})+(?:,\d+)?|\d+(?:,\d+)?")


def _nombres(txt: str) -> Counter:
    return Counter(re.sub(r"[ \u202f\u00a0]", "", m) for m in RE_NOMBRE.findall(txt or ""))


def fiabilite_nombres(md: str, texte_natif: str) -> float | None:
    """Part des nombres de l'OCR (≥ 3 chiffres) présents dans la couche texte native.
    None si la page n'a pas de couche texte (page scannée : rien à comparer)."""
    natifs = _nombres(texte_natif)
    if sum(natifs.values()) < 5:
        return None
    ocr = [k for k in _nombres(md) if len(k.replace(",", "")) >= 3]
    if not ocr:
        return 1.0
    return round(sum(k in natifs for k in ocr) / len(ocr), 2)
