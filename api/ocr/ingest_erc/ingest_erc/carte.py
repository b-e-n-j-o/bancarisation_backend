"""Carte du document : le LLM découvre la structure, le code l'applique.

1. `compacter(doc)`      : document entier, pages balisées, grosses matrices tronquées
2. `llm.cartographier()` : un appel (ou un par paquet si le document dépasse le budget)
                           -> CarteDocument (vocabulaire, fiches, tableaux, sections…)
3. `verifier(carte, doc)`: chaque élément cite un extrait + une page ; on vérifie
                           qu'il existe (page ±1). Non trouvé => écarté et signalé.
4. `Regles.depuis(carte)`: motifs regex construits à partir du vocabulaire du document
                           (préfixes d'unités, familles de codes, libellés de champs).
Sans carte (pas de clé), `Regles.defaut()` reproduit le comportement historique.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import get_args, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .inventaire import norm
from .mdnorm import RE_IMG, cellules, est_ligne_tableau

log = logging.getLogger(__name__)

# ================================================================== schéma
# GLM envoie souvent `"colonnes": null` : default_factory ne s'applique pas à null.
class _IgnoreNull(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _drop_null(cls, data):
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data


def _literal(typ, defaut):
    allowed = set(get_args(typ))

    def _ok(v):
        return v if v in allowed else defaut
    return _ok


class Citation(_IgnoreNull):
    page: int
    texte: str = Field(description="extrait VERBATIM court (5 à 15 mots) copié du document")


class Famille(_IgnoreNull):
    prefixe: str = Field(description="préfixe du code tel qu'écrit, ex. 'TU', 'GH', 'A'")
    libelle: str = Field(description="nom de la famille, ex. 'travaux uniques'")
    citation: Optional[Citation] = None


class Vocabulaire(_IgnoreNull):
    unite_terme: str = Field(description="nom des unités spatiales de gestion, ex. 'unité de gestion', 'secteur'")
    unite_prefixes: list[str] = Field(description="préfixes des identifiants d'unités tels qu'écrits, ex. ['UG'] ou ['S', 'Secteur']")
    unite_exemples: list[str] = Field(default_factory=list, description="2-4 identifiants cités, ex. ['UG 1', 'UG 2, UG 4 et UG 6']")
    action_forme: Literal["code", "libelle"] = "code"
    action_familles: list[Famille] = Field(default_factory=list)
    action_exemples: list[str] = Field(default_factory=list, description="2-4 codes d'actions cités, ex. ['TU 1', 'GH 03']")

    @field_validator("action_forme", mode="before")
    @classmethod
    def _forme(cls, v):
        return _literal(Literal["code", "libelle"], "code")(v)


class FicheRef(_IgnoreNull):
    code: str
    titre: str = Field(description="titre de la fiche tel qu'écrit (sans le code)")
    page: int


class ChampsFiche(_IgnoreNull):
    unites: list[str] = Field(default_factory=list, description="libellés du champ listant les unités, ex. ['Unités de gestion']")
    parcelles: list[str] = Field(default_factory=list)
    objectif: list[str] = Field(default_factory=list)
    periode: list[str] = Field(default_factory=list)
    fin_entete: list[str] = Field(default_factory=list, description="libellés marquant la fin de l'en-tête descriptif, ex. ['Constat et justification']")


class ColonnesTableau(_IgnoreNull):
    code: Optional[str] = None
    unite: Optional[str] = None
    intitule: Optional[str] = None
    cible: Optional[str] = None
    objectif: Optional[str] = None
    mesure: Optional[str] = None
    surface: Optional[str] = None
    section: Optional[str] = None
    numero: Optional[str] = None
    subdivision: Optional[str] = None


RoleTableau = Literal["synthese_actions", "synthese_unites", "synthese_mesures", "parcelles", "planning",
                      "couts", "frise", "autre"]


class TableauRef(_IgnoreNull):
    role: RoleTableau = "autre"
    page: int
    titre: Optional[str] = None
    colonnes: ColonnesTableau = Field(default_factory=ColonnesTableau,
                                      description="libellé EXACT de l'en-tête pour chaque rôle de colonne")
    pages_suite: list[int] = Field(default_factory=list)

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, v):
        return _literal(RoleTableau, "autre")(v)


RoleSection = Literal["programme_actions", "unites_gestion", "fiches", "planning", "couts",
                      "parcelles", "contexte", "annexe", "autre"]


class SectionRef(_IgnoreNull):
    role: RoleSection = "autre"
    titre: str
    page_debut: int
    page_fin: int

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, v):
        return _literal(RoleSection, "autre")(v)


class Conventions(_IgnoreNull):
    etat_zero: Optional[int] = None
    annee_N: Optional[int] = None
    annee_fin: Optional[int] = None
    montants: Optional[Literal["HT", "TTC", "mixte", "inconnu"]] = None
    citations: list[Citation] = Field(default_factory=list)

    @field_validator("montants", mode="before")
    @classmethod
    def _montants(cls, v):
        if v is None:
            return None
        return v if v in ("HT", "TTC", "mixte", "inconnu") else "inconnu"


class AnnexeRef(_IgnoreNull):
    titre: str
    page_debut: int
    page_fin: int
    type: Literal["arrete", "budget", "cartographie", "autre"] = "autre"

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v):
        return _literal(Literal["arrete", "budget", "cartographie", "autre"], "autre")(v)


TypeRemarque = Literal["valeur_du_projet", "incoherence_document", "qualite_de_lecture"]


class Remarque(_IgnoreNull):
    texte: str = Field(description="l'observation, en une phrase, avec les valeurs en cause")
    type: TypeRemarque = Field(
        "incoherence_document",
        description="valeur_du_projet = contredit une valeur du projet (n° d'arrêté, années, HT/TTC) ; "
                    "incoherence_document = erreur du dossier sans effet sur ces valeurs ; "
                    "qualite_de_lecture = défaut de conversion/OCR")
    page: Optional[int] = Field(None, description="page où l'observation se constate")
    citation: Optional[str] = Field(None, description="extrait VERBATIM qui la prouve")
    verifiee: bool = False          # rempli par verifier()

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v):
        return _literal(TypeRemarque, "incoherence_document")(v)


class CarteDocument(_IgnoreNull):
    vocabulaire: Vocabulaire
    fiches: list[FicheRef] = Field(default_factory=list)
    champs_fiche: ChampsFiche = Field(default_factory=ChampsFiche)
    tableaux: list[TableauRef] = Field(default_factory=list)
    sections: list[SectionRef] = Field(default_factory=list)
    conventions: Conventions = Field(default_factory=Conventions)
    annexes: list[AnnexeRef] = Field(default_factory=list)
    remarques: list[Remarque] = Field(default_factory=list)
    verification: dict = Field(default_factory=dict)  # rempli par verifier()

    @field_validator("remarques", mode="before")
    @classmethod
    def _remarques_texte(cls, v):     # anciennes cartes en cache : simples chaînes
        out = []
        for x in (v or []):
            if x is None:
                continue
            out.append({"texte": x} if isinstance(x, str) else x)
        return out

    @model_validator(mode="before")
    @classmethod
    def _listes(cls, data):
        if not isinstance(data, dict):
            return data
        data = {k: v for k, v in data.items() if v is not None}
        tabs = data.get("tableaux")
        if isinstance(tabs, list):
            data["tableaux"] = [
                t for t in tabs
                if isinstance(t, dict) and t.get("page") is not None
            ]
        return data


# ================================================================== compaction
MAX_LIGNES_TABLEAU = int(os.environ.get("CARTE_MAX_LIGNES_TABLEAU", "12"))


def _compacter_markdown(md: str) -> str:
    out, bloc = [], []

    def vider():
        if not bloc:
            return
        rows = [l for l in bloc if not re.match(r"^\s*\|?\s*:?-{3,}", l)]
        annees = sum(bool(re.fullmatch(r"(19|20)\d\d", c)) for c in cellules(rows[0])) if rows else 0
        if len(rows) > MAX_LIGNES_TABLEAU or annees >= 5:
            out.extend(rows[:4])
            out.append(f"[… tableau tronqué : {len(rows)} lignes au total]")
        else:
            out.extend(rows)
        bloc.clear()

    for l in (md or "").splitlines():
        if est_ligne_tableau(l):
            bloc.append(l)
            continue
        vider()
        l = RE_IMG.sub("", l)
        if l.strip() or (out and out[-1].strip()):
            out.append(l)
    vider()
    return "\n".join(out).strip()


def compacter(doc) -> str:
    bruit = set(doc.lignes_repetees or [])
    parts = []
    for p in doc.pages:
        src = p.markdown if p.markdown else p.texte
        txt = "\n".join(l for l in _compacter_markdown(src).splitlines()
                        if re.sub(r"\s+\d{1,3}\s*$", "", l.strip()) not in bruit)
        parts.append(f"=== page {p.num} ===\n{txt}")
    return "\n\n".join(parts)


def estimer_tokens(txt: str) -> int:
    return len(txt) // 3


# ================================================================== vérification
def _trouve(extrait: str, doc, page: int, marge: int = 1) -> Optional[int]:
    cible = norm(re.sub(r"[*#|_]", " ", extrait))[:60]
    if len(cible) < 4:
        return None
    proches = sorted((p for p in doc.pages if abs(p.num - page) <= marge), key=lambda p: abs(p.num - page))
    for p in proches:                      # la page annoncée d'abord, puis les voisines
        if cible in norm(re.sub(r"[*#|_]", " ", (p.markdown or "") + "\n" + p.texte)):
            return p.num
    return None


def verifier(carte: CarteDocument, doc) -> CarteDocument:
    """Écarte tout ce qui n'est pas retrouvé dans le document (anti-hallucination)."""
    rapport = {"fiches": [0, 0], "tableaux": [0, 0], "familles": [0, 0], "conventions": [0, 0], "ecartes": []}

    fiches = []
    for f in carte.fiches:
        rapport["fiches"][1] += 1
        pg = _trouve(f.titre[:50], doc, f.page, marge=2)
        if pg:
            f.page = pg
            fiches.append(f)
            rapport["fiches"][0] += 1
        else:
            rapport["ecartes"].append(f"fiche {f.code} « {f.titre[:40]} » p.{f.page}")
    carte.fiches = fiches

    tabs = []
    for t in carte.tableaux:
        rapport["tableaux"][1] += 1
        libs = [v for v in t.colonnes.model_dump().values() if v]
        ok = all(_trouve(l, doc, t.page, marge=0) for l in libs) if libs else bool(t.titre and _trouve(t.titre, doc, t.page))
        if ok or t.role == "frise":
            tabs.append(t)
            rapport["tableaux"][0] += 1
        else:
            rapport["ecartes"].append(f"tableau {t.role} p.{t.page} (en-têtes introuvables)")
    carte.tableaux = tabs

    fams = []
    for f in carte.vocabulaire.action_familles:
        rapport["familles"][1] += 1
        if f.citation and _trouve(f.citation.texte, doc, f.citation.page):
            fams.append(f)
            rapport["familles"][0] += 1
        elif any(re.search(rf"\b{re.escape(f.prefixe)}\s?-?\s?\d", p.texte) for p in doc.pages):
            f.citation = None      # préfixe bien présent, libellé non vérifié
            fams.append(f)
            rapport["familles"][0] += 1
        else:
            rapport["ecartes"].append(f"famille {f.prefixe}")
    carte.vocabulaire.action_familles = fams

    cv = carte.conventions
    rapport["conventions"][1] = len(cv.citations)
    prouve = {str(v) for v in (cv.etat_zero, cv.annee_N, cv.annee_fin) if v}
    gardees = []
    for c in cv.citations:
        if not _trouve(c.texte, doc, c.page):
            rapport["ecartes"].append(f"citation p.{c.page} introuvable")
        elif prouve & set(re.findall(r"\b\d{4}\b", c.texte)) or \
                re.search(r"(?i)[ée]tat z[ée]ro|ann[ée]e\s*N\b", c.texte):
            gardees.append(c)          # la citation doit porter l'année qu'elle prouve
        else:
            rapport["ecartes"].append(f"citation hors sujet p.{c.page} : « {c.texte[:50]} »")
    cv.citations = gardees
    rapport["conventions"][0] = len(cv.citations)

    rapport["remarques"] = [0, len(carte.remarques)]
    for r in carte.remarques:
        r.verifiee = not r.citation or bool(_trouve(r.citation, doc, r.page or 1, marge=2))
        rapport["remarques"][0] += r.verifiee
    if not cv.citations:
        cv.etat_zero = cv.annee_N = cv.annee_fin = None   # non prouvé => pas utilisé
    carte.verification = rapport
    return carte


# ================================================================== règles dérivées
def _alternance(libelles: list[str]) -> str:
    libs = sorted({l.strip().rstrip(":").strip() for l in libelles if l and l.strip()}, key=len, reverse=True)
    return "|".join(re.sub(r"\\ ", r"\\s+", re.escape(l)) for l in libs)


@dataclass
class Regles:
    re_unite: re.Pattern
    canon_unite: str
    re_code: re.Pattern
    familles: dict = field(default_factory=dict)
    champs: dict = field(default_factory=dict)       # nom -> regex de libellé
    re_fin_entete: Optional[re.Pattern] = None
    source: str = "defaut"
    alt_unite: str = "UG"

    @classmethod
    def defaut(cls) -> "Regles":
        return cls(
            re_unite=re.compile(r"\bUG\s?-?\s?(\d{1,3}[a-z]?)\b", re.I), canon_unite="UG",
            re_code=re.compile(r"\b([A-Z]{2,4})\s?(\d{1,2}[a-z]?)\b"),
            champs={"unites": r"unit[ée]s?\s+de\s+gestion", "parcelles": r"parcelles\s+concern[ée]es",
                    "objectif": r"objectif\s+[àa]\s+long\s+terme"},
            re_fin_entete=re.compile(r"(?i)constat et justification|description de la mesure"),
            alt_unite="UG")

    @classmethod
    def depuis(cls, carte: CarteDocument) -> "Regles":
        v = carte.vocabulaire
        prefs = sorted({p.strip() for p in v.unite_prefixes if p.strip()}, key=len, reverse=True)
        canon = re.sub(r"[^A-Za-z]", "", prefs[-1]).upper() if prefs else "UG"
        alt_u = "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in prefs) or "UG"
        re_unite = re.compile(rf"\b(?:{alt_u})\s?(?:n°\s?)?-?\s?(\d{{1,3}}[a-z]?)\b", re.I)
        fams = {f.prefixe.strip(): f.libelle for f in v.action_familles}
        alt_c = "|".join(re.escape(p) for p in sorted(fams, key=len, reverse=True))
        re_code = re.compile(rf"\b({alt_c})\s?[-.]?\s?(\d{{1,3}}[a-z]?)\b") if alt_c else cls.defaut().re_code
        c = carte.champs_fiche
        d = cls.defaut()
        champs = {k: (_alternance(getattr(c, k)) or d.champs.get(k)) for k in ("unites", "parcelles", "objectif", "periode")}
        champs = {k: v for k, v in champs.items() if v}
        fin = _alternance(c.fin_entete)
        return cls(re_unite=re_unite, canon_unite=canon, re_code=re_code, familles=fams, champs=champs,
                   re_fin_entete=re.compile(rf"(?i){fin}") if fin else d.re_fin_entete, source="carte",
                   alt_unite=alt_u)

    def unites(self, txt: str) -> list[str]:
        s = str(txt or "")
        out = [f"{self.canon_unite}{n.upper()}" for n in self.re_unite.findall(s)]
        for a, b in re.findall(rf"(?i)\b(?:{self.alt_unite})\s?(\d{{1,3}})\s*(?:à|au|-)\s*(?:{self.alt_unite})?\s?(\d{{1,3}})\b", s):
            if int(a) < int(b) <= int(a) + 30:
                for n in range(int(a), int(b) + 1):
                    if f"{self.canon_unite}{n}" not in out:
                        out.append(f"{self.canon_unite}{n}")
        m = self.re_unite.search(s)
        if m:   # « UG 2, UG 4 et 6 » : numéros orphelins après la première unité
            for n in re.findall(r"(?:,|\bet\b)\s*(\d{1,3})\b(?!\s*(?:ha|m|%))", s[m.end():]):
                if f"{self.canon_unite}{n}" not in out:
                    out.append(f"{self.canon_unite}{n}")
        def cle(u):
            return (int(re.sub(r"\D", "", u) or 0), u)
        return sorted(dict.fromkeys(out), key=cle)

    def code(self, txt: str) -> Optional[str]:
        m = self.re_code.fullmatch(str(txt or "").strip()) or self.re_code.match(str(txt or "").strip())
        if not m:
            return None
        num = m.group(2).lstrip("0") or "0"
        return f"{m.group(1).upper()}{num}"


def charger_ou_cartographier(doc, cache_dir) -> Optional[CarteDocument]:
    """Cache par sha256 ; appel LLM seulement si une clé est disponible."""
    from . import llm
    cache = cache_dir / f"{doc.sha256}.carte.json"
    if cache.exists():
        return verifier(CarteDocument.model_validate_json(cache.read_text()), doc)
    if not llm.actif():
        return None
    texte = compacter(doc)
    t0 = time.time()
    carte = llm.cartographier(texte)          # gère le découpage si trop long
    carte = verifier(carte, doc)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(carte.model_dump_json(indent=1))
    v = carte.verification
    msg = (f"carte {doc.nom} : fiches {v['fiches'][0]}/{v['fiches'][1]}, "
           f"tableaux {v['tableaux'][0]}/{v['tableaux'][1]}, "
           f"familles {v['familles'][0]}/{v['familles'][1]}, "
           f"conventions {v['conventions'][0]}/{v['conventions'][1]}, "
           f"remarques {v.get('remarques', [0, 0])[0]}/{v.get('remarques', [0, 0])[1]}, "
           f"unités={'/'.join(carte.vocabulaire.unite_prefixes)}, "
           f"{len(v['ecartes'])} écart(s), {time.time() - t0:.1f} s, "
           f"~{estimer_tokens(texte)} tokens")
    log.info(msg)
    print(f"🗺️  {msg}", flush=True)
    return carte


def fusionner(cartes: list[CarteDocument]) -> CarteDocument:
    """Fusion des cartes partielles (document découpé en paquets de pages)."""
    base = cartes[0].model_copy(deep=True)
    for c in cartes[1:]:
        v, bv = c.vocabulaire, base.vocabulaire
        bv.unite_prefixes = list(dict.fromkeys(bv.unite_prefixes + v.unite_prefixes))
        vus = {f.prefixe for f in bv.action_familles}
        bv.action_familles += [f for f in v.action_familles if f.prefixe not in vus]
        base.fiches += c.fiches
        base.tableaux += c.tableaux
        base.sections += c.sections
        base.annexes += c.annexes
        base.remarques += c.remarques
        for k in ("unites", "parcelles", "objectif", "periode", "fin_entete"):
            setattr(base.champs_fiche, k, list(dict.fromkeys(getattr(base.champs_fiche, k) + getattr(c.champs_fiche, k))))
        for k in ("etat_zero", "annee_N", "annee_fin", "montants"):
            if getattr(base.conventions, k) is None:
                setattr(base.conventions, k, getattr(c.conventions, k))
        base.conventions.citations += c.conventions.citations
    return base
