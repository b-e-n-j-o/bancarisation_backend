"""Étape 2 — Découpage déterministe de l'acte (aucun LLM).

Structure classique d'un arrêté préfectoral : en-tête (autorité, titre, référence),
VU…, CONSIDÉRANT…, « ARRÊTE », ARTICLE 1 … ARTICLE n (avec sous-articles 11.2,
16.1…), bloc de signature (« Bordeaux, le … », « Pour le préfet… »), annexes.
Repère aussi les légendes de figures et la référence de l'acte.
Les variantes courantes sont couvertes : « Article 1er », « ART. 3 – », « Article 2 - Objet »,
titres sur deux lignes, sous-articles « 12.1 : » ou « ARTICLE 16.1 : », TITRE / CHAPITRE.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .s1_ingest import DocumentText
from .schemas import Figure

ART_RE = re.compile(
    r"^\s*(?:ARTICLE|Article|ART\.|Art\.)\s+(premier|1er|\d+(?:[.\-]\d+)*(?:\s*(?:bis|ter|quater))?)"
    r"\s*(?:[:.\-–—]\s*(.*)|\s*$|\s+([A-ZÉÈÀ].*))$")
SUB_RE = re.compile(r"^\s*(\d+(?:\.\d+){1,3})\s*[:.\-–—]?\s+([A-ZÉÈÀÂ«].{2,160})$")
SECTION_RE = re.compile(r"^\s*(TITRE|CHAPITRE|SECTION)\s+([IVXLC\d]+)\b\s*[:.\-–—]?\s*(.*)$")
VU_RE = re.compile(r"^\s*V\s?U\b\s*[:,]?\s*(.*)$", re.I)
CONS_RE = re.compile(r"^\s*CONSID[ÉE]RANT\b\s*(.*)$", re.I)
DECIDE_RE = re.compile(r"^\s*(A\s*R\s*R\s*[ÊE]\s*T\s*E|D\s*[ÉE]\s*C\s*I\s*D\s*E)\s*:?\s*$")
SIGN_RE = re.compile(r"^\s*(?:Fait\s+à\s+)?([A-ZÉ][\w\-’' ]{2,40}),?\s+le\s+(\d{1,2}(?:er)?\s+\w+\s+\d{4})\s*$")
ANNEX_RE = re.compile(r"^\s*ANNEXE\s*(\w+)?\s*[:.\-–—]?\s*(.*)$")
FIG_RE = re.compile(r"^\s*(Figure|Fig\.|Carte|Plan|Illustration)\s*(\d+)\s*[:.\-–—]\s*(.{3,200})$", re.I)
REF_RE = re.compile(r"(?:R[ée]f(?:[ée]rence)?\.?|N°|n°)\s*[A-Z]*\s*:?\s*n?\s*°?\s*([A-Z0-9][\w\-/ ]{1,30}\d)")
MONTHS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
          "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12}


def fr_date(s: str) -> str | None:
    m = re.search(r"(\d{1,2})(?:er)?\s+([a-zéû]+)\s+(\d{4})", s.lower())
    if m and m.group(2) in MONTHS:
        return f"{int(m.group(3)):04d}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


class ArticleSeg(BaseModel):
    numero: str
    titre: str = ""
    parent: str | None = None
    niveau: int = 1
    section: str = ""
    page_debut: int = 0
    page_fin: int = 0
    texte: str = ""                     # avec marqueurs [PAGE n] aux changements de page


class Segmented(BaseModel):
    entete: str = ""
    reference: str = ""
    visas: list[str] = []
    considerants: list[str] = []
    articles: list[ArticleSeg] = []
    signature_texte: str = ""
    signature_lieu: str = ""
    signature_date: str | None = None
    annexes: list[str] = []
    figures: list[Figure] = []
    issues: list[str] = []


def _main_num(numero: str) -> int:
    if numero.lower() in ("premier", "1er"):
        return 1
    m = re.match(r"\d+", numero)
    return int(m.group()) if m else -1


def _norm_num(numero: str) -> str:
    n = numero.lower().replace("-", ".").replace(" ", "")
    return "1" if n in ("premier", "1er") else n


def segment(doc: DocumentText) -> Segmented:
    lines = [(p.number, l) for p in doc.pages for l in p.text.splitlines()]
    seg = Segmented()
    state, cur_block = "entete", None
    blocks: list[tuple[str, list[tuple[int, str]]]] = []   # (type, lignes)
    last_main, section = 0, ""
    cur_parent: str | None = None
    i = 0
    while i < len(lines):
        page, line = lines[i]
        s = line.strip()
        m_art = ART_RE.match(line)
        if m_art and state in ("entete", "visas", "considerants", "decide", "article", "signature"):
            num = _norm_num(m_art.group(1))
            main = _main_num(num)
            if main >= last_main and main - last_main <= 3:      # garde-fou contre les renvois en début de ligne
                title = (m_art.group(2) or m_art.group(3) or "").strip()
                # titre sur deux lignes : la ligne suivante courte, sans point final, minuscule au début
                if i + 1 < len(lines):
                    nxt = lines[i + 1][1].strip()
                    if title and not title.endswith((".", ":")) and nxt and len(nxt) < 70 and nxt[0].islower():
                        title += " " + nxt
                        i += 1
                level = num.count(".") + 1
                parent = ".".join(num.split(".")[:-1]) if level > 1 else None
                if level == 1:
                    cur_parent = num
                art = ArticleSeg(numero=num, titre=title, parent=parent, niveau=level, section=section,
                                 page_debut=page, page_fin=page)
                seg.articles.append(art)
                cur_block = ("article", [])
                blocks.append(cur_block)
                state, last_main = "article", main
                i += 1
                continue
        if state == "article" and cur_parent:
            m_sub = SUB_RE.match(line)
            if m_sub and m_sub.group(1).split(".")[0] == cur_parent and len(s) < 170:
                num = m_sub.group(1)
                seg.articles.append(ArticleSeg(numero=num, titre=m_sub.group(2).strip(), parent=".".join(num.split(".")[:-1]),
                                               niveau=num.count(".") + 1, section=section, page_debut=page, page_fin=page))
                cur_block = ("article", [])
                blocks.append(cur_block)
                i += 1
                continue
        m_sec = SECTION_RE.match(line)
        if m_sec and state in ("decide", "article"):
            section = f"{m_sec.group(1).title()} {m_sec.group(2)} {m_sec.group(3)}".strip()
            i += 1
            continue
        if state != "article" or not seg.articles:
            if VU_RE.match(line) and state in ("entete", "visas"):
                state = "visas"
                cur_block = ("visa", [(page, VU_RE.match(line).group(1))])
                blocks.append(cur_block)
                i += 1
                continue
            if CONS_RE.match(line):
                state = "considerants"
                cur_block = ("considerant", [(page, CONS_RE.match(line).group(1))])
                blocks.append(cur_block)
                i += 1
                continue
            if DECIDE_RE.match(line):
                state = "decide"
                cur_block = ("decide", [])
                blocks.append(cur_block)
                i += 1
                continue
        m_sign = SIGN_RE.match(line)
        if m_sign and state == "article":
            state = "signature"
            seg.signature_lieu, seg.signature_date = m_sign.group(1).strip(), fr_date(m_sign.group(2))
            cur_block = ("signature", [(page, s)])
            blocks.append(cur_block)
            i += 1
            continue
        if ANNEX_RE.match(line) and state in ("signature", "article") and len(s) < 120 and s.upper().startswith("ANNEXE"):
            state = "annexe"
            cur_block = ("annexe", [(page, s)])
            blocks.append(cur_block)
            i += 1
            continue
        m_fig = FIG_RE.match(line)
        if m_fig:
            seg.figures.append(Figure(numero=m_fig.group(2), legende=m_fig.group(3).strip(), page=page,
                                      article=seg.articles[-1].numero if seg.articles and state == "article" else None))
        if cur_block is None:
            cur_block = ("entete", [])
            blocks.append(cur_block)
        cur_block[1].append((page, line))
        i += 1

    # --- assemblage
    def join(lns: list[tuple[int, str]], with_pages: bool) -> str:
        out, last = [], None
        for pg, l in lns:
            if with_pages and pg != last:
                out.append(f"[PAGE {pg}]")
                last = pg
            out.append(l.rstrip())
        txt = "\n".join(out)
        return re.sub(r"\n{3,}", "\n\n", txt).strip()

    art_iter = iter(seg.articles)
    for typ, lns in blocks:
        if typ == "entete":
            seg.entete += join(lns, False) + "\n"
        elif typ == "visa":
            seg.visas.append(re.sub(r"\s+", " ", join(lns, False)).strip(" ,;"))
        elif typ == "considerant":
            seg.considerants.append(re.sub(r"\s+", " ", join(lns, False)).strip(" ,;"))
        elif typ == "article":
            a = next(art_iter)
            a.texte = join(lns, True)
            if lns:
                a.page_debut = min(a.page_debut, lns[0][0])
                a.page_fin = max(pg for pg, _ in lns)
        elif typ == "signature":
            seg.signature_texte = join(lns, False)
        elif typ == "annexe":
            seg.annexes.append(join(lns, True))
    seg.visas = [v for v in seg.visas if v]
    m = REF_RE.search(seg.entete)
    seg.reference = m.group(1).strip() if m else ""

    # --- contrôles de structure
    mains = sorted({_main_num(a.numero) for a in seg.articles if a.niveau == 1})
    if not seg.articles:
        seg.issues.append("Aucun article détecté : découpage à vérifier (document non standard ou OCR à revoir).")
    elif mains != list(range(1, mains[-1] + 1)):
        missing = sorted(set(range(1, mains[-1] + 1)) - set(mains))
        seg.issues.append(f"Numérotation d'articles discontinue : articles manquants {missing}.")
    for a in seg.articles:
        if not a.texte.strip() and not any(b.parent == a.numero for b in seg.articles):
            seg.issues.append(f"Article {a.numero} sans texte.")
    if not seg.signature_date:
        seg.issues.append("Date de signature non repérée par le découpage (sera cherchée par le LLM).")
    return seg
