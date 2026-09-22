"""Passe 3 — extraction lourde : du référentiel verrouillé au projet structuré.

Entrées : ReferentielVerrouille (étape 2.5), documents inventoriés, cartes PDF + règles,
          classeurs Excel (via le profileur).
Sortie  : ResultatPasse3 = échéances, occurrences (budget posé quand le grain le permet),
          non plaçables, budget non ventilé, rejets, avertissements. Rien n'est écrit en base.

Principes : aucune règle propre à un BE ; le LLM ne produit que des valeurs contraintes
(enums du référentiel) et justifiées par une citation vérifiée ; ce qui ne rentre pas
dans les contrats devient un rejet ou un avertissement, jamais une valeur devinée.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from openpyxl.utils import get_column_letter
from pydantic import BaseModel, Field, create_model, field_validator

from . import llm
from .carte import Regles
from .carte_classeur import (
    CarteClasseur, TableauBrut, TableauCarte, _col_temps,
    carte_repli, charger_carte_cache, detecter_tableaux, verifier_classeur,
)
from .excel_profil import _annee, lire_grille
from .inventaire import norm
from .modeles import ReferentielVerrouille, Source
from .pdf_sections import decouper_fiches

from api.ocr.calcul_occurrences import generer
from api.ocr.models import (Echeance, FenetreIntervention, Palier as PalierMoteur,
                            Recurrence, Source as SourceMoteur)

Methode = Literal["deterministe", "llm", "user"]


# ══════════════════════════════════════════════════════════════════ 1. FAITS
class Borne(BaseModel):
    """Date absolue OU relative à l'année N validée (N=0, N+5=5, état zéro=-1)."""
    annee: Optional[int] = None
    rang: Optional[int] = None


class Palier(BaseModel):
    intervalle_ans: float
    nombre_occurrences: int


class Fenetre(BaseModel):
    mois_debut: Optional[int] = Field(None, ge=1, le=12)
    mois_fin: Optional[int] = Field(None, ge=1, le=12)


class Temporalite(BaseModel):
    forme: Literal["annees", "rangs", "regle", "evenement", "inconnu"]
    annees: list[int] = Field(default_factory=list)
    rangs: list[int] = Field(default_factory=list)
    type_regle: Optional[Literal["ponctuel", "periodique", "paliers", "campagnes"]] = None
    debut: Optional[Borne] = None
    fin: Optional[Borne] = None
    intervalle_ans: Optional[float] = None
    paliers: list[Palier] = Field(default_factory=list)
    occurrences_par_an: Optional[int] = None
    duree_ans: Optional[int] = None
    evenement: Optional[str] = None
    fenetre: Optional[Fenetre] = None


class FaitTemporel(BaseModel):
    code: str
    ugs: Optional[list[str]] = None             # None = UG de l'action
    temporalite: Temporalite
    libelle: Optional[str] = None               # variante (« 2e éclaircie »)
    role_source: str = "fiche"                  # planning_passages | planning_montants | fiche…
    bloc: Optional[str] = None
    source: Source
    methode: Methode = "deterministe"
    confiance_extraction: float = 1.0
    avertissements: list[str] = Field(default_factory=list)


class FaitBudget(BaseModel):
    """Un montant AU GRAIN OÙ IL A ÉTÉ DONNÉ. Rien n'est réparti à l'extraction."""
    grain: Literal["action_annee", "action", "projet"]
    code: Optional[str] = None
    ugs: Optional[list[str]] = None
    periode: Optional[Borne] = None
    montant: float
    taxe: Literal["HT", "TTC", "inconnu"] = "inconnu"
    nature: Literal["prevu", "realise"] = "prevu"
    prestataire: Optional[str] = None
    bloc: Optional[str] = None
    source: Source
    avertissements: list[str] = Field(default_factory=list)


class FaitStatut(BaseModel):
    code: str
    periode: Borne
    statut: Literal["realise", "planifie", "supprime", "repousse"]
    source: Source


class Rejet(BaseModel):
    motif: Literal["code_inconnu", "ug_inconnue", "rang_sans_ancre", "montant_base_inconnue",
                   "citation_introuvable", "tableau_non_lu"]
    detail: str
    source: Source


# ══════════════════════════════════════════════════════════════════ 2. RATTACHEMENT
def forme_canonique(code) -> str:
    """« TU 01 » -> « TU1 », « ug-5 » -> « UG5 ». Hors motif lettres+chiffres : inchangé."""
    s = unicodedata.normalize("NFKD", str(code)).encode("ascii", "ignore").decode().strip()
    m = re.fullmatch(r"([A-Za-z]+)[\s\-_.]*0*(\d+)([a-z]?)", s)
    return f"{m.group(1).upper()}{m.group(2)}{m.group(3)}" if m else s


class Contexte:
    """Le référentiel verrouillé vu par les extracteurs : clés fermées, ancre, conventions."""

    def __init__(self, ref: ReferentielVerrouille, regles: Optional[Regles] = None):
        self.ref = ref
        self.regles = regles or Regles.defaut()
        self.actions = {a.code: a for a in ref.actions}
        self.ugs = {u.ug_code for u in ref.ugs}
        d = ref.decisions or {}
        self.alias_codes = {forme_canonique(k): v for k, v in (d.get("alias_codes") or {}).items()}
        self.requalifications = dict(d.get("requalifications_blocs") or {})
        self.commentaires = list(d.get("commentaires") or [])
        self._codes = {forme_canonique(c): c for c in self.actions}
        self._ugs = {forme_canonique(u): u for u in self.ugs}
        p = ref.projet
        self.annee_N = p.get("annee_N")
        self.annee_fin = p.get("annee_fin")
        self.duree = p.get("duree_ans")
        self.conventions = {v["source"]: v for k, v in p.items()
                            if k.startswith("convention_montants") and isinstance(v, dict) and v.get("source")}

    def code(self, brut, bloc: Optional[str] = None) -> Optional[str]:
        if bloc and bloc in self.requalifications:
            brut = self.requalifications[bloc]
        if brut in (None, ""):
            return None
        c = forme_canonique(brut)
        return self._codes.get(forme_canonique(self.alias_codes.get(c, c)))

    def ug(self, brut) -> Optional[str]:
        return self._ugs.get(forme_canonique(brut))

    def annee(self, b: Optional[Borne]) -> Optional[int]:
        if b is None:
            return None
        if b.annee is not None:
            return b.annee
        if b.rang is not None and self.annee_N is not None:
            return int(self.annee_N) + b.rang
        return None

    def ugs_action(self, code: str) -> list[str]:
        a = self.actions.get(code)
        return list(a.ugs) if a else []

    def commentaires_pour(self, codes=(), ugs=()) -> list[str]:
        cibles = set(codes) | set(ugs)
        out = []
        for c in self.commentaires:
            portees = set(str(c.get("portee", "")).split("+"))
            if "projet" in portees or portees & cibles:
                out.append(f"[{str(c.get('enonce', ''))[:120]}] {c.get('texte', '')}")
        return out


def rattacher(faits: list, cx: Contexte) -> tuple[list, list[Rejet]]:
    """Met chaque fait aux clés du référentiel ; ce qui ne s'y rattache pas est rejeté."""
    ok, rejets = [], []

    def rejet(motif, detail, f):
        rejets.append(Rejet(motif=motif, detail=detail, source=f.source))

    for f in faits:
        upd = {}
        if getattr(f, "code", None) is not None or getattr(f, "bloc", None) in cx.requalifications:
            code = cx.code(f.code, getattr(f, "bloc", None))
            if code is None:
                rejet("code_inconnu", f"code « {f.code} » absent du référentiel", f)
                continue
            upd["code"] = code
        if getattr(f, "ugs", None):
            ugs = [cx.ug(u) for u in f.ugs]
            if None in ugs:
                rejet("ug_inconnue", f"UG {f.ugs} hors référentiel", f)
                continue
            upd["ugs"] = sorted(set(ugs))
        if isinstance(f, FaitTemporel):
            t = f.temporalite
            if t.forme == "rangs":
                if cx.annee_N is None:
                    rejet("rang_sans_ancre", "rangs relatifs sans année N validée", f)
                    continue
                t = t.model_copy(update={"forme": "annees", "rangs": [],
                                         "annees": sorted({int(cx.annee_N) + r for r in t.rangs})})
            elif t.forme == "regle":
                bornes = {}
                for champ in ("debut", "fin"):
                    b = getattr(t, champ)
                    if b is not None and b.annee is None:
                        a = cx.annee(b)
                        if a is None:
                            break
                        bornes[champ] = Borne(annee=a)
                else:
                    t = t.model_copy(update=bornes)
                if any(getattr(t, c) is not None and getattr(t, c).annee is None for c in ("debut", "fin")):
                    rejet("rang_sans_ancre", "règle relative sans année N validée", f)
                    continue
            upd["temporalite"] = t
        if isinstance(f, (FaitBudget, FaitStatut)) and f.periode is not None and f.periode.annee is None:
            a = cx.annee(f.periode)
            if a is None:
                rejet("rang_sans_ancre", "période relative sans année N validée", f)
                continue
            upd["periode"] = Borne(annee=a)
        if isinstance(f, FaitBudget):
            conv = cx.conventions.get(f.source.doc)
            taxe = f.taxe if f.taxe != "inconnu" else ((conv or {}).get("lignes") or "inconnu")
            if taxe == "TTC":
                k = (conv or {}).get("coefficient")
                if not k:
                    rejet("montant_base_inconnue", "montant TTC sans taux connu pour cette source", f)
                    continue
                upd["montant"] = round(f.montant / k, 2)
            elif taxe != "HT":
                rejet("montant_base_inconnue", "base HT/TTC inconnue pour cette source", f)
                continue
            upd["taxe"] = "HT"
        ok.append(f.model_copy(update=upd))
    return ok, rejets


# ══════════════════════════════════════════════════════════════════ 4. EXTRACTEURS
def _num(v) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v or "")).replace(",", ".")
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


STATUTS = [(r"r[ée]alis|fait|termin", "realise"), (r"supprim|abandon|annul", "supprime"),
           (r"report|d[ée]cal", "repousse"), (r"pr[ée]vu|projet|planifi", "planifie")]


def _statut(v) -> Optional[str]:
    s = norm(str(v or ""))
    return next((st for motif, st in STATUTS if re.search(motif, s)), None)


def correspondances_libelles(libelles: set[str], cx: Contexte) -> dict[str, str]:
    """Libellés d'actions sans code -> code du référentiel : 1 appel LLM, sortie contrainte."""
    reste = sorted(l for l in libelles if cx.code(l) is None)
    if not reste or not llm.actif() or not cx.actions:
        return {}
    Code = Literal[tuple(sorted(cx.actions)) + ("aucun",)]  # type: ignore[valid-type]
    Corr = create_model("Correspondance", libelle=(str, ...), code=(Code, ...))
    Sortie = create_model("Correspondances", correspondances=(list[Corr], Field(default_factory=list)))
    actions = "\n".join(f"- {a.code} : {a.intitule} (UG {', '.join(a.ugs)})" for a in cx.ref.actions)
    user = json.dumps({"actions_du_projet": actions, "libelles": reste,
                       "schema": Sortie.model_json_schema()}, ensure_ascii=False)
    sortie = llm._appel(
        "Associe chaque libellé de tableur à l'action du projet qu'il désigne, ou « aucun » si "
        "aucune ne correspond clairement. Ne force jamais une correspondance.",
        user, Sortie, **_role("libelles"))
    return {c.libelle: c.code for c in sortie.correspondances if c.code != "aucun" and c.libelle in reste}


def extraire_tableau(f, t: TableauBrut, tc: TableauCarte, cols: dict, cx: Contexte,
                     corr: dict[str, str]) -> tuple[list, list[Rejet]]:
    faits, rejets = [], []
    if "action" not in cols:
        rejets.append(Rejet(motif="tableau_non_lu", detail=f"{t.id} ({tc.role}) : pas de colonne action",
                            source=Source(doc=t.doc, loc=t.feuille)))
        return faits, rejets
    temps = {}
    decalage = set()
    if tc.orientation == "annees_en_colonnes":
        for c, lib in t.entetes.items():
            an, rg = _col_temps(lib)
            if rg is not None:
                temps[c] = Borne(rang=rg)                   # l'ancre validée fait foi
                if an is not None and cx.annee_N is not None and an != int(cx.annee_N) + rg:
                    decalage.add(an - (int(cx.annee_N) + rg))
            elif an is not None:
                temps[c] = Borne(annee=an)
    avert = [f"en-têtes du tableau décalés de {d:+d} an(s) par rapport à l'ancre validée ; rangs retenus"
             for d in sorted(decalage)]
    montants = tc.role in ("planning_montants", "decompte_realise", "couts_unitaires")
    nature = "realise" if tc.role == "decompte_realise" else "prevu"

    for r in t.lignes:
        brut = f.get(r, cols["action"]).v
        if brut in (None, ""):
            continue                                        # ligne de total / séparateur
        brut = str(brut).strip()
        code = corr.get(brut, brut)
        ugs = cx.regles.unites(f.get(r, cols["ug"]).v) if "ug" in cols else None
        presta = str(f.get(r, cols["prestataire"]).v) if "prestataire" in cols and f.get(r, cols["prestataire"]).v else None
        loc = lambda c: f"{t.feuille}!{get_column_letter(c)}{r}"
        src = Source(doc=t.doc, loc=loc(cols["action"]), extrait=brut[:60])
        commun = dict(code=code, ugs=ugs or None, bloc=t.id)

        if tc.orientation == "annees_en_colonnes" and temps:
            bornes = []
            for c, b in temps.items():
                cel = f.get(r, c)
                if cel.typ == "vide" or (cel.typ == "num" and not cel.v):
                    continue
                if montants and cel.typ == "num":
                    faits.append(FaitBudget(grain="action_annee", periode=b, montant=float(cel.v), taxe=tc.taxe,
                                            nature=nature, prestataire=presta, avertissements=avert,
                                            source=src.model_copy(update={"loc": loc(c)}), **commun))
                if tc.role == "statuts" and _statut(cel.v):
                    faits.append(FaitStatut(code=code, periode=b, statut=_statut(cel.v),
                                            source=src.model_copy(update={"loc": loc(c)})))
                bornes.append(b)
            if bornes and tc.role in ("planning_passages", "planning_montants"):
                faits.append(FaitTemporel(
                    temporalite=Temporalite(forme="rangs", rangs=sorted({b.rang for b in bornes}))
                    if all(b.rang is not None for b in bornes)
                    else Temporalite(forme="annees", annees=sorted({cx.annee(b) for b in bornes if cx.annee(b)})),
                    role_source=tc.role, source=src, avertissements=avert,
                    confiance_extraction=1.0 if tc.role == "planning_passages" else 0.8, **commun))

        elif tc.orientation == "annees_en_lignes" and "annee" in cols:
            an = _annee(f.get(r, cols["annee"]).v)
            if an is None:
                continue
            b = Borne(annee=an)
            m = _num(f.get(r, cols["montant"]).v) if "montant" in cols else None
            if montants and m:
                faits.append(FaitBudget(grain="action_annee", periode=b, montant=m, taxe=tc.taxe, nature=nature,
                                        prestataire=presta, source=src, **commun))
            if "statut" in cols and _statut(f.get(r, cols["statut"]).v):
                faits.append(FaitStatut(code=code, periode=b, statut=_statut(f.get(r, cols["statut"]).v), source=src))
            if tc.role in ("planning_passages", "planning_montants"):
                faits.append(FaitTemporel(temporalite=Temporalite(forme="annees", annees=[an]),
                                          role_source=tc.role, source=src, **commun))

        elif tc.role == "couts_unitaires":
            m = _num(f.get(r, cols["montant"]).v) if "montant" in cols else None
            if m is None and {"quantite", "prix_unitaire"} <= set(cols):
                q, pu = _num(f.get(r, cols["quantite"]).v), _num(f.get(r, cols["prix_unitaire"]).v)
                m = q * pu if q is not None and pu is not None else None
            if m:
                faits.append(FaitBudget(grain="action", montant=m, taxe=tc.taxe, prestataire=presta,
                                        source=src, **commun))
    return faits, rejets


def _role(nom: str) -> dict:
    """Modèle / effort / max tokens d'un rôle de config.ROLES, avec repli sur « carte »."""
    from . import config
    try:
        return {"modele": config.modele_pour(nom), "effort": config.effort_pour(nom),
                "max_tokens": config.max_tokens_pour(nom)}
    except (KeyError, ValueError):
        return {"modele": config.modele_pour("carte"), "effort": config.effort_pour("carte"),
                "max_tokens": config.max_tokens_pour("carte")}


def extraire_classeur(doc, cx: Contexte, cache_dir: Optional[Path],
                      carte: Optional[CarteClasseur] = None) -> tuple[list, list[Rejet], dict]:
    """Lit les lignes selon la carte déjà validée (passe 1). Pas d'appel LLM de cartographie."""
    feuilles = lire_grille(doc.chemin)
    tabs = detecter_tableaux(feuilles, doc.nom)
    if carte is None:
        carte = charger_carte_cache(doc, cache_dir)
    if carte is None:
        carte = carte_repli(tabs, feuilles, cx.regles)
    roles = (cx.ref.decisions or {}).get("roles_tableaux") or {}
    for tc in carte.tableaux:
        if tc.id in roles:
            tc.role = roles[tc.id]
    carte, cols = verifier_classeur(carte, tabs)
    par_nom, par_id = {f.nom: f for f in feuilles}, {t.id: t for t in tabs}
    lus = [tc for tc in carte.tableaux if tc.role not in ("synthese", "parcelles", "autre") and "action" in cols.get(tc.id, {})]
    libelles = {str(par_nom[par_id[tc.id].feuille].get(r, cols[tc.id]["action"]).v).strip()
                for tc in lus for r in par_id[tc.id].lignes
                if par_nom[par_id[tc.id].feuille].get(r, cols[tc.id]["action"]).v not in (None, "")}
    corr = correspondances_libelles(libelles, cx)
    faits, rejets = [], []
    for tc in lus:
        t = par_id[tc.id]
        fs, rj = extraire_tableau(par_nom[t.feuille], t, tc, cols[tc.id], cx, corr)
        faits += fs
        rejets += rj
    resume = {"tableaux_detectes": len(tabs), "carte": carte.verification,
              "roles": {tc.id: tc.role for tc in carte.tableaux}, "libelles_associes": corr}
    return faits, rejets, resume


# ---------------------------------------------------------------- fiches (LLM)
PROMPT_FICHE = """Tu lis les fiches actions d'un plan de gestion, chacune balisée === FICHE CODE === ; attribue chaque échéance au code de SA fiche.
Décris QUAND chaque action a lieu, uniquement d'après ce qui est écrit :
- annees : années absolues écrites ; rangs : années relatives (N=0, N+5=5, état zéro=-1) ;
- regle : récurrence décrite (ponctuel, periodique avec intervalle, paliers, campagnes = K fois
  par an pendant M ans), avec début et fin si écrits ;
- evenement : dépend d'un événement (ex. « après la première éclaircie ») ;
- inconnu : rien de daté.
Fenêtre d'intervention (mois) seulement si elle est écrite.
Chaque échéance cite MOT POUR MOT le passage qui la justifie : la citation est vérifiée.
Réponds uniquement avec un JSON conforme au schéma fourni."""


def schema_fiche(cx: Contexte) -> tuple[type[BaseModel], type[BaseModel]]:
    Code = Literal[tuple(sorted(cx.actions)) or ("_",)]      # type: ignore[valid-type]
    UG = Literal[tuple(sorted(cx.ugs)) or ("_",)]            # type: ignore[valid-type]
    EcheanceLue = create_model(
        "EcheanceLue",
        code=(Code, ...),
        ugs=(Optional[list[UG]], None),
        libelle=(Optional[str], None),
        temporalite=(Temporalite, ...),
        citation=(str, ""),
    )
    ExtractionFiche = create_model(
        "ExtractionFiche", echeances=(list[EcheanceLue], Field(default_factory=list))
    )
    return ExtractionFiche, EcheanceLue


class ExtractionFicheBrute(BaseModel):
    """Parse souple : un item mal formé n'abat pas le lot."""
    echeances: list[dict] = Field(default_factory=list)

    @field_validator("echeances", mode="before")
    @classmethod
    def _dicts_seuls(cls, v):
        if not isinstance(v, list):
            return []
        return [x for x in v if isinstance(x, dict)]


def _plat(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def citation_trouvee(citation: str, texte: str) -> bool:
    c, t = _plat(citation), _plat(texte)
    if not c:
        return False
    if c in t:
        return True
    mots, vocab = c.split(), set(t.split())
    return len(mots) >= 4 and sum(m in vocab for m in mots) / len(mots) >= 0.8


def extraire_fiches(doc, carte, cx: Contexte, cache_dir: Optional[Path] = None,
                    budget_car: int = 60000) -> tuple[list, list[Rejet]]:
    """Fiches lues par lots (≈ 1 à 2 appels par plan). Citations vérifiées fiche par fiche."""
    if carte is None or not llm.actif():
        return [], []
    faits, rejets = [], []
    Sch, EcheanceLue = schema_fiche(cx)
    schema = json.dumps(Sch.model_json_schema(), ensure_ascii=False)
    fiches, lots, cur, taille = {}, [], [], 0
    for s in decouper_fiches(doc.pages, carte, cx.regles):
        code = cx.code(s.code)
        if code is None:
            rejets.append(Rejet(motif="code_inconnu", detail=f"fiche « {s.titre[:60]} »",
                                source=Source(doc=doc.nom, loc=f"p.{s.page_debut}-{s.page_fin}")))
            continue
        fiches[code] = s
        bloc = s.texte[:15000]
        if cur and taille + len(bloc) > budget_car:
            lots.append(cur); cur, taille = [], 0
        cur.append(code); taille += len(bloc)
    if cur:
        lots.append(cur)

    for lot in lots:
        entete = [f"Année N du projet : {cx.annee_N}. Horizon : {cx.annee_fin}.", "Actions du lot :"]
        entete += [f"- {c} = {cx.actions[c].intitule} (UG {', '.join(cx.ugs_action(c))})" for c in lot]
        notes = cx.commentaires_pour(lot, [u for c in lot for u in cx.ugs_action(c)])
        if notes:
            entete += ["Précisions du bureau d'études (contexte) :"] + [f"- {n}" for n in notes]
        corps = "\n\n".join(f"=== FICHE {c} (p.{fiches[c].page_debut}-{fiches[c].page_fin}) ===\n"
                            f"{fiches[c].texte[:15000]}" for c in lot)
        user = "\n".join(entete) + "\n\n" + corps + "\n\nSchéma JSON :\n" + schema
        cle = hashlib.sha256((PROMPT_FICHE + user).encode()).hexdigest()[:16]
        cache = cache_dir / f"{doc.sha256}.fiches.{cle}.json" if cache_dir else None
        if cache and cache.exists():
            brute = ExtractionFicheBrute.model_validate_json(cache.read_text())
        else:
            try:
                brute = llm._appel(PROMPT_FICHE, user, ExtractionFicheBrute, **_role("fiche"))
            except Exception as err:  # noqa: BLE001
                print(f"   ⚠️  lot fiches {lot} : {err}", flush=True)
                for c in lot:
                    s = fiches[c]
                    rejets.append(Rejet(
                        motif="citation_introuvable",
                        detail=f"{c} : lot non extrait ({str(err)[:180]})",
                        source=Source(doc=doc.nom, loc=f"p.{s.page_debut}-{s.page_fin}"),
                    ))
                continue
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(brute.model_dump_json())
        for raw in brute.echeances:
            raw = {**raw, "citation": raw.get("citation") or raw.get("extrait") or ""}
            try:
                e = EcheanceLue.model_validate(raw)
            except Exception as err:  # noqa: BLE001
                rejets.append(Rejet(
                    motif="citation_introuvable",
                    detail=f"{raw.get('code', '?')} : échéance illisible ({str(err)[:120]})",
                    source=Source(doc=doc.nom, loc="lot", extrait=str(raw.get("citation") or "")[:120]),
                ))
                continue
            citation = e.citation or ""
            s = fiches.get(e.code)
            src = Source(doc=doc.nom, loc=f"p.{s.page_debut}-{s.page_fin}" if s else "lot",
                         extrait=citation[:120])
            if s is None or not citation_trouvee(citation, s.texte):
                rejets.append(Rejet(motif="citation_introuvable",
                                    detail=f"{e.code} : citation absente de sa fiche", source=src))
                continue
            faits.append(FaitTemporel(code=e.code, ugs=e.ugs, libelle=e.libelle,
                                      temporalite=e.temporalite, role_source="fiche",
                                      source=src, methode="llm", confiance_extraction=0.7))
    return faits, rejets


# ══════════════════════════════════════════════════════════════════ 5. CALENDRIER
class PlanEcheance(BaseModel):
    code: str
    libelle: Optional[str] = None
    ugs: list[str]
    temporalite: Temporalite
    sources: list[Source]
    confiance: float
    champs_a_confirmer: list[str] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)


def annees_indicatives(t: Temporalite, fin_globale: Optional[int]) -> set[int]:
    """Années qu'une temporalité produirait (sert à comparer une fiche au tableur)."""
    if t.forme == "annees":
        return set(t.annees)
    if t.forme != "regle" or not t.debut or t.debut.annee is None:
        return set()
    d = t.debut.annee
    f = (t.fin.annee if t.fin and t.fin.annee else None) or fin_globale or d
    if t.type_regle == "ponctuel":
        return {d}
    if t.type_regle == "periodique":
        return set(range(d, f + 1, max(1, round(t.intervalle_ans or 1))))
    if t.type_regle == "campagnes":
        return {a for a in range(d, d + max(1, t.duree_ans or 1)) if a <= f}
    if t.type_regle == "paliers":
        out, der = {d}, d
        for p in t.paliers:
            for _ in range(p.nombre_occurrences):
                der += max(1, round(p.intervalle_ans))
                if der > f:
                    return out
                out.add(der)
        return out
    return set()


def construire_calendrier(temporels: list[FaitTemporel], cx: Contexte) -> list[PlanEcheance]:
    plans = []
    par_code: dict[str, list[FaitTemporel]] = {}
    for f in temporels:
        par_code.setdefault(f.code, []).append(f)
    for code in sorted(cx.actions):
        fs = par_code.get(code, [])
        ugs_def = cx.ugs_action(code)
        passages = [f for f in fs if f.role_source == "planning_passages"]
        montants = [f for f in fs if f.role_source == "planning_montants"]
        fiches = [f for f in fs if f.role_source == "fiche"]
        base = passages or montants
        if base:
            annees = sorted({a for f in base for a in f.temporalite.annees})
            ugs = sorted({u for f in base for u in (f.ugs or [])}) or ugs_def
            avert = sorted({w for f in base for w in f.avertissements})
            if len({tuple(f.temporalite.annees) for f in base}) > 1:
                avert.append("lignes du tableur aux calendriers différents (parcelles ?) : années fusionnées")
            # témoins : un tableau de montants compte pour UN témoin (union de ses lignes)
            temoins = []
            if passages:
                par_bloc: dict = {}
                for f in montants:
                    par_bloc.setdefault(f.bloc, []).append(f)
                for fs_b in par_bloc.values():
                    temoins.append(fs_b[0].model_copy(update={"temporalite": Temporalite(
                        forme="annees", annees=sorted({a for x in fs_b for a in x.temporalite.annees}))}))
            temoins += [f for f in fiches if f.temporalite.forme in ("annees", "regle")]
            accords = []
            for f in temoins:
                att = annees_indicatives(f.temporalite, cx.annee_fin)
                if att:
                    part = len(att & set(annees)) / len(att | set(annees))
                    accords.append(part >= 0.8)
                    if part < 0.8:
                        avert.append(f"{f.source.doc} {f.source.loc} décrit {sorted(att)[:8]} ; "
                                     f"tableur retenu ({annees[:8]})")
            conf = 0.9 if accords and all(accords) else (0.75 if passages else 0.65)
            plans.append(PlanEcheance(code=code, ugs=ugs, temporalite=Temporalite(forme="annees", annees=annees),
                                      sources=[f.source for f in base][:6] + [f.source for f in temoins][:3],
                                      confiance=conf, avertissements=avert))
            extras = [f for f in fiches if f.temporalite.forme == "evenement"]
        else:
            extras = fiches
        vus = set()
        for f in extras:
            cle = (f.libelle, f.temporalite.model_dump_json())
            if cle in vus:
                continue
            vus.add(cle)
            plans.append(PlanEcheance(
                code=code, libelle=f.libelle, ugs=f.ugs or ugs_def, temporalite=f.temporalite,
                sources=[f.source], confiance=0.6 if f.temporalite.forme in ("annees", "regle") else 0.4,
                champs_a_confirmer=[] if f.temporalite.forme in ("annees", "regle") else ["ancrage_annee"],
                avertissements=f.avertissements))
        if not base and not extras:
            plans.append(PlanEcheance(code=code, ugs=ugs_def, temporalite=Temporalite(forme="inconnu"),
                                      sources=[], confiance=0.0, champs_a_confirmer=["recurrence"],
                                      avertissements=["aucune date trouvée dans les documents"]))
    return plans


# ══════════════════════════════════════════════════════════════════ 6. PROJECTION
def _recurrence(t: Temporalite) -> Recurrence:
    debut = t.debut.annee if t.debut else None
    fin = t.fin.annee if t.fin else None
    if t.forme == "annees" and t.annees:
        return Recurrence(type="explicite", annees=sorted(set(t.annees)), ancrage_annee=min(t.annees))
    if t.forme == "regle" and t.type_regle:
        return Recurrence(type=t.type_regle, ancrage_annee=debut, annee_fin=fin, intervalle_ans=t.intervalle_ans,
                          occurrences_par_an=t.occurrences_par_an, duree_ans=t.duree_ans,
                          paliers=[PalierMoteur(**p.model_dump()) for p in t.paliers])
    return Recurrence(type="dependant_evenement", ancrage_annee=None,
                      regle_source=t.evenement or "aucune date écrite")


def vers_echeances(plans: list[PlanEcheance], cx: Contexte) -> list[Echeance]:
    out = []
    for p in plans:
        cle = hashlib.sha1(f"{p.code}|{p.libelle or ''}|{','.join(p.ugs)}".encode()).hexdigest()[:10]
        a = cx.actions[p.code]
        f = p.temporalite.fenetre
        s0 = p.sources[0] if p.sources else None
        page = re.match(r"p\.(\d+)", s0.loc) if s0 else None
        out.append(Echeance(
            id=f"{p.code}-{cle}", code_operation=p.code,
            type_operation=re.match(r"[A-Za-z]+", p.code).group(0).upper() if re.match(r"[A-Za-z]+", p.code) else p.code,
            type_metier="autre",
            libelle=(a.intitule or p.code) + (f" — {p.libelle}" if p.libelle else ""),
            ug_ids=p.ugs, recurrence=_recurrence(p.temporalite),
            fenetre_intervention=FenetreIntervention(
                debut=f"{f.mois_debut:02d}-01" if f and f.mois_debut else None,
                fin=f"{f.mois_fin:02d}-28" if f and f.mois_fin else None,
                traverse_nouvel_an=bool(f and f.mois_debut and f.mois_fin and f.mois_fin < f.mois_debut)),
            duree_gestion_ans=cx.duree,
            source=SourceMoteur(page=int(page.group(1)) if page else None, extrait=s0.extrait if s0 else None,
                                doc=s0.doc if s0 else None, loc=s0.loc if s0 else None),
            confiance=round(p.confiance, 2), champs_a_confirmer=p.champs_a_confirmer,
            avertissements=p.avertissements))
    return out


# ══════════════════════════════════════════════════════════════════ 7. BUDGET
def poser_budget(occurrences: list, budgets: list[FaitBudget], code_de: dict[str, str]):
    """Montant d'une (action, année) posé sur l'occurrence unique correspondante ; sinon la
    ligne reste non ventilée, au grain où elle a été donnée."""
    somme: dict[tuple, dict] = {}
    non_ventile = []
    for b in budgets:
        if b.grain != "action_annee" or b.periode is None:
            non_ventile.append({"grain": b.grain, "code": b.code, "montant_ht": b.montant, "nature": b.nature,
                                "prestataire": b.prestataire, "source": b.source.model_dump(),
                                "motif": "montant global (pas d'année)"})
            continue
        k = (b.code, b.periode.annee, b.nature)
        e = somme.setdefault(k, {"par_bloc": {}, "prestataire": b.prestataire, "sources": []})
        e["par_bloc"][b.bloc] = e["par_bloc"].get(b.bloc, 0.0) + b.montant    # lignes d'un même tableau
        e["sources"].append(b.source)
    index: dict[tuple, list] = {}
    for i, o in enumerate(occurrences):
        index.setdefault((code_de.get(o.echeance_cle), o.annee), []).append(i)
    avert = []
    for (code, an, nature), e in somme.items():
        valeurs = list(e["par_bloc"].values())
        montant = round(valeurs[0], 2)
        if len({round(v, 2) for v in valeurs}) > 1:
            avert.append(f"{code} {an} : montants différents selon les tableaux {sorted(round(v, 2) for v in valeurs)} ; "
                         f"premier retenu")
        cibles = index.get((code, an), [])
        if len(cibles) == 1:
            o = occurrences[cibles[0]]
            upd = {"montant_ht": montant} if nature == "prevu" else {"montant_realise": montant}
            if e["prestataire"] and not getattr(o, "prestataire", None):
                upd["prestataire"] = e["prestataire"]
            occurrences[cibles[0]] = o.model_copy(update=upd)
        else:
            non_ventile.append({"grain": "action_annee", "code": code, "annee": an, "montant_ht": montant,
                                "nature": nature, "prestataire": e["prestataire"],
                                "source": e["sources"][0].model_dump(),
                                "motif": "aucune occurrence cette année" if not cibles
                                else f"{len(cibles)} occurrences cette année (à répartir)"})
    return occurrences, non_ventile, avert


# ══════════════════════════════════════════════════════════════════ POINT D'ENTRÉE
class ResultatPasse3(BaseModel):
    empreinte_referentiel: str
    annee_fin: int
    echeances: list[dict]
    occurrences: list[dict]
    non_placables: list[dict]
    budget_non_ventile: list[dict]
    rejets: list[Rejet]
    avertissements: list[str]
    classeurs: dict = Field(default_factory=dict)
    stats: dict = Field(default_factory=dict)


def executer_passe3(ref: ReferentielVerrouille, docs: list, cartes_pdf: Optional[dict] = None,
                    regles: Optional[Regles] = None, cache_dir: Optional[Path] = None,
                    cartes_classeur: Optional[dict] = None,
                    annee_courante: Optional[int] = None) -> ResultatPasse3:
    cx = Contexte(ref, regles)
    faits, rejets, classeurs = [], [], {}
    for d in docs:
        if d.role == "tableur" and d.extension.lower().lstrip(".") in ("xlsx", "xlsm"):
            fs, rj, resume = extraire_classeur(d, cx, cache_dir, (cartes_classeur or {}).get(d.nom))
            faits += fs
            rejets += rj
            classeurs[d.nom] = resume
        elif d.role == "plan_gestion":
            fs, rj = extraire_fiches(d, (cartes_pdf or {}).get(d.nom), cx, cache_dir)
            faits += fs
            rejets += rj

    faits, rj = rattacher(faits, cx)
    rejets += rj
    temporels = [f for f in faits if isinstance(f, FaitTemporel)]
    budgets = [f for f in faits if isinstance(f, FaitBudget)]
    statuts = [f for f in faits if isinstance(f, FaitStatut)]

    plans = construire_calendrier(temporels, cx)
    echeances = vers_echeances(plans, cx)
    annee_fin = int(cx.annee_fin or max([a for p in plans for a in p.temporalite.annees] or [date.today().year]))
    occurrences, non_placables = generer(echeances, annee_fin=annee_fin, annee_courante=annee_courante)

    code_de = {e.id: e.code_operation for e in echeances}
    faits_statut = {(s.code, s.periode.annee): s.statut for s in statuts}
    for i, o in enumerate(occurrences):
        st = faits_statut.get((code_de.get(o.echeance_cle), o.annee))
        if st and st != "planifie":
            occurrences[i] = o.model_copy(update={"statut": st})

    occurrences, non_ventile, avert_budget = poser_budget(occurrences, budgets, code_de)
    avertissements = avert_budget + [f"{p.code} : {w}" for p in plans for w in p.avertissements]
    return ResultatPasse3(
        empreinte_referentiel=ref.empreinte, annee_fin=annee_fin,
        echeances=[e.model_dump(mode="json") for e in echeances],
        occurrences=[o.model_dump(mode="json") for o in occurrences],
        non_placables=[e.model_dump(mode="json") for e in non_placables],
        budget_non_ventile=non_ventile, rejets=rejets, avertissements=avertissements,
        classeurs=classeurs,
        stats={"faits": len(faits), "temporels": len(temporels), "budgets": len(budgets),
               "statuts": len(statuts), "echeances": len(echeances), "occurrences": len(occurrences),
               "non_placables": len(non_placables), "rejets": len(rejets),
               "occurrences_avec_montant": sum(getattr(o, "montant_ht", None) is not None for o in occurrences)})