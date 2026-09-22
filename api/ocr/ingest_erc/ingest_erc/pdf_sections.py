"""Sélection des sections d'un plan de gestion à envoyer au LLM (passe 1).

1. plan du document : titres markdown (#) si OCR Mistral, sinon titres numérotés
   et en-têtes de fiches « CODE n : intitulé » détectés dans le texte ;
2. apprentissage des familles de codes d'action du document (TU/TE/SE/MG ici,
   GH/SE/PI ailleurs…) = préfixes qui cohabitent avec une UG sur une même ligne ;
3. score par section ; on garde le meilleur sous un budget de caractères.
Le routeur LLM (llm.choisir_sections) n'est appelé que si aucun score n'est net.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import llm
from .cadastre import refs_dans_texte
from .inventaire import norm

RE_UG = re.compile(r"\bUG\s?\d{1,3}\b")
RE_TOKEN_CODE = re.compile(r"\b([A-Z]{2,4})\s?(\d{1,2})\b")
RE_TITRE_NUM = re.compile(r"^\s{0,12}((?:\d{1,2}\.){1,3}\d{0,2}\.?|[IVX]{1,4}\.)\s+(\S.{3,120})$")
# « 6.3.1. Travaux uniques (TU)    40 » : ligne de sommaire (numérotation + n° de page final)
RE_LIGNE_TDM = re.compile(
    r"^\s*(?:#\s*)?(?:(?:\d{1,2}\.){1,3}\d{0,2}\.?|[IVX]{1,4}\.|fig\.|tabl\.).{5,}?\s{2,}\d{1,3}\s*$",
    re.I,
)
MOTS_CLES = ["unite de gestion", "unites de gestion", "synthese", "programme d'action",
             "parcelles concernees", "objectif", "tableau de synthese", "etat zero", "annee n"]


@dataclass
class Section:
    id: int
    titre: str
    page_debut: int
    page_fin: int
    lignes: list = field(default_factory=list)   # (page, texte)
    score: float = 0.0
    signaux: dict = field(default_factory=dict)

    fiche: bool = False
    re_fin: object = None       # fin d'en-tête de fiche (règles du document)
    code: str = ""

    @property
    def texte(self) -> str:
        return "\n".join(t for _, t in self.lignes)

    @property
    def extrait(self) -> str:
        """Pour une fiche action : seulement l'en-tête (jusqu'au « Constat »)."""
        if not self.fiche:
            return self.texte
        out = []
        fin = self.re_fin or re.compile(r"(?i)constat et justification|description de la mesure")
        for i, (_, t) in enumerate(self.lignes):
            if i > 0 and fin.search(t):
                break
            if t.strip():
                out.append(t.rstrip())
        return "\n".join(out[:25] if not self.re_fin else out)


def familles_codes(pages) -> dict[str, set]:
    """Préfixe retenu s'il cohabite au moins une fois avec une UG sur une ligne
    et s'il apparaît avec ≥ 2 numéros dans le document."""
    avec_ug, nums = set(), defaultdict(set)
    for p in pages:
        for ligne in p.texte.splitlines():
            toks = [(pre, int(n)) for pre, n in RE_TOKEN_CODE.findall(ligne) if pre != "UG"]
            for pre, n in toks:
                nums[pre].add(n)
            if RE_UG.search(ligne):
                avec_ug.update(pre for pre, _ in toks)
    return {k: nums[k] for k in avec_ug if len(nums[k]) >= 2}


def decouper(pages, familles) -> list[Section]:
    re_fiche = re.compile(rf"^\s{{0,6}}({'|'.join(familles) or 'ZZZ'})\s?(\d{{1,2}})\s*:\s*(.+)$")
    sections, cur = [], Section(0, "(début)", 1, 1)
    for p in pages:
        for ligne in p.texte.splitlines():
            if "...." in ligne or RE_LIGNE_TDM.match(ligne):   # table des matières
                cur.lignes.append((p.num, ligne))
                continue
            m_md = re.match(r"^#{1,6}\s+(.+)", ligne)
            nu = m_md.group(1) if m_md else ligne       # titre sans marqueur markdown
            m_f = re_fiche.match(nu)
            m_n = RE_TITRE_NUM.match(nu)
            if m_f or m_n:
                m_md = None
            titre = (m_md.group(1).strip() if m_md else
                     f"{m_f.group(1)}{m_f.group(2)} : {m_f.group(3).strip()}" if m_f else
                     f"{m_n.group(1)} {m_n.group(2).strip()}" if m_n and len(ligne.strip()) < 120 else None)
            if titre:
                cur.page_fin = p.num
                sections.append(cur)
                cur = Section(len(sections), titre, p.num, p.num, fiche=bool(m_f))
            cur.lignes.append((p.num, ligne))
        cur.page_fin = p.num
    sections.append(cur)
    return [s for s in sections if s.lignes]


def scorer(s: Section, familles) -> None:
    txt = s.texte
    codes_ug = sum(1 for l in txt.splitlines()
                   if RE_UG.search(l) and any(pre in familles for pre, _ in RE_TOKEN_CODE.findall(l)))
    n = norm(txt)
    kw = sum(n.count(k) for k in MOTS_CLES)
    refs = len(refs_dans_texte(txt))
    tdm = txt.count("....") > 5 or sum(bool(RE_LIGNE_TDM.match(l)) for l in txt.splitlines()) > 5
    s.signaux = {"lignes_code_ug": codes_ug, "mots_cles": kw, "refs_cadastrales": refs, "tdm": tdm}
    s.score = 0 if tdm else codes_ug * 3 + kw * 1.5 + min(refs, 10) * 0.5 + (8 if s.fiche else 0)


def selectionner(pages, budget_car: int = 30000) -> dict:
    fam = familles_codes(pages)
    secs = decouper(pages, fam)
    for s in secs:
        scorer(s, fam)
    classees = sorted(secs, key=lambda s: -s.score)
    retenues, total = [], 0
    for s in classees:
        if s.score < 3:
            break
        if total + len(s.extrait) > budget_car:
            continue
        retenues.append(s)
        total += len(s.extrait)
    net = bool(classees) and classees[0].score >= 10
    plan = [{"id": s.id, "titre": s.titre[:90], "pages": f"{s.page_debut}-{s.page_fin}",
             "car": len(s.texte), "fiche": s.fiche, "score": round(s.score, 1), **s.signaux}
            for s in secs]
    routeur_llm_requis = not net
    routeur_llm_utilise = False
    if routeur_llm_requis and llm.actif():
        try:
            by_id = {s.id: s for s in secs}
            ids = llm.choisir_sections(plan)
            choisies, total_llm = [], 0
            for i in ids:
                s = by_id.get(i)
                if not s:
                    continue
                if total_llm + len(s.extrait) > budget_car:
                    continue
                choisies.append(s)
                total_llm += len(s.extrait)
            if choisies:
                retenues, total = choisies, total_llm
                routeur_llm_utilise = True
        except Exception as err:  # noqa: BLE001
            print(f"   ⚠️  routeur LLM ignoré : {err}", flush=True)
    return {
        "familles": {k: sorted(v) for k, v in fam.items()},
        "plan": plan,
        "retenues": sorted(retenues, key=lambda s: s.id),
        "car_retenus": total,
        "car_total": sum(len(p.texte) for p in pages),
        "routeur_llm_requis": routeur_llm_requis,
        "routeur_llm_utilise": routeur_llm_utilise,
    }


# ================================================================== pilotage par la carte
def _lignes(pages):
    return [(p.num, l) for p in pages for l in p.texte.splitlines()]


def decouper_fiches(pages, carte, regles) -> list[Section]:
    """Localise chaque fiche de la carte (titre cherché sur sa page) et découpe le texte
    jusqu'au début de la fiche suivante ou de la prochaine section de la carte."""
    lignes = _lignes(pages)
    debuts = []
    for f in sorted(carte.fiches, key=lambda f: f.page):
        cible = norm(f.titre)[:40]
        code_n = re.sub(r"\s+", "", norm(f.code))

        def score(i, pg, l, c):
            nl = norm(l)
            if not c or c not in nl:
                return None
            return (abs(pg - f.page), 0 if l.lstrip().startswith("#") else 1,
                    0 if code_n in re.sub(r"\s+", "", nl) else 1, i)

        idx = None
        for c in (cible, cible[:20]):      # titre éventuellement coupé sur 2 lignes
            cands = [x for x in (score(i, pg, l, c) for i, (pg, l) in enumerate(lignes) if abs(pg - f.page) <= 1) if x]
            if cands:
                idx = min(cands)[3]
                break
        if idx is not None:
            debuts.append((idx, f))
    debuts.sort(key=lambda x: x[0])
    bornes_sections = sorted({s.page_debut for s in carte.sections})
    out = []
    for k, (idx, f) in enumerate(debuts):
        fin = debuts[k + 1][0] if k + 1 < len(debuts) else len(lignes)
        pg0 = lignes[idx][0]
        fin_sec = next((b for b in bornes_sections if b > pg0), None)
        if fin_sec:
            fin = min(fin, next((i for i, (pg, _) in enumerate(lignes) if pg >= fin_sec), fin))
        sec = Section(id=k, titre=f"{f.code} : {f.titre}", page_debut=pg0, page_fin=lignes[fin - 1][0],
                      lignes=lignes[idx:fin], fiche=True, re_fin=regles.re_fin_entete,
                      code=regles.code(f.code) or f.code)
        out.append(sec)
    return out


def selectionner_par_carte(pages, carte, regles, budget_car: int = 60000) -> dict:
    fiches = decouper_fiches(pages, carte, regles)
    utiles = {"programme_actions", "unites_gestion"}
    secs = []
    for s in carte.sections:
        if s.role in utiles:
            sp = [p for p in pages if s.page_debut <= p.num <= s.page_fin]
            secs.append(Section(id=len(secs), titre=s.titre, page_debut=s.page_debut, page_fin=s.page_fin,
                                lignes=[(p.num, l) for p in sp for l in p.texte.splitlines()]))
    for t in carte.tableaux:
        if t.role in ("synthese_actions", "synthese_mesures"):
            for n in [t.page] + t.pages_suite:
                if not any(s.page_debut <= n <= s.page_fin for s in secs):
                    p = next((x for x in pages if x.num == n), None)
                    if p:
                        secs.append(Section(id=len(secs), titre=t.titre or t.role, page_debut=n, page_fin=n,
                                            lignes=[(n, l) for l in p.texte.splitlines()]))
    retenues, total = [], 0
    for s in secs + fiches:
        if total + len(s.extrait) <= budget_car:
            retenues.append(s)
            total += len(s.extrait)
    return {
        "familles": {k: [] for k in regles.familles},
        "plan": [{"id": i, "titre": s.titre[:90], "pages": f"{s.page_debut}-{s.page_fin}",
                  "car": len(s.texte), "fiche": s.fiche, "source": "carte"} for i, s in enumerate(retenues)],
        "retenues": retenues,
        "car_retenus": total,
        "car_total": sum(len(p.texte) for p in pages),
        "routeur_llm_requis": False,
        "routeur_llm_utilise": False,
        "fiches_localisees": f"{len(fiches)}/{len(carte.fiches)}",
    }
