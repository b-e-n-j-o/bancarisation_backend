"""Fiche arrêté : le LLM lit la décision, le code vérifie chaque citation.

1. `charger_ou_lire_arrete(doc, cache_dir, pages)` : texte compacté de la décision (arrêté
   déposé, ou pages d'une décision embarquée en annexe du plan) -> 1 appel LLM -> FicheArrete
2. `verifier_arrete(fiche, doc)` : chaque élément cite un extrait + une page ; non retrouvé
   => écarté. Pour les valeurs chiffrées (référence, date, durée, surface), la valeur doit
   en plus figurer dans sa propre citation.
3. `faits_depuis_fiche(fiche, doc, cle)` : faits methode="llm" AUX MÊMES TYPES que les regex
   de `faits_arrete`. La réconciliation confronte les deux lectures : concordance =
   confiance haute, divergence = question. La fiche entière part aussi en fait
   `fiche_arrete` : elle rejoint le référentiel verrouillé (decisions["arrete"]) et la
   passe 3 ne relit jamais l'arrêté.

L'arrêté fixe des OBLIGATIONS ; il ne planifie pas les actions du BE. Les échéanciers des
prescriptions servent au contrôle de couverture, jamais à créer des occurrences.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Literal, Optional

from pydantic import Field, field_validator

from .carte import Citation, _IgnoreNull, _literal, _trouve, compacter
from .inventaire import norm
from .modeles import Fait, Source

log = logging.getLogger(__name__)


# ================================================================== schéma
class Champ(_IgnoreNull):
    valeur: Optional[str] = None
    citation: Optional[Citation] = None


class Identite(_IgnoreNull):
    reference: Champ = Field(default_factory=Champ, description="numéro de la décision tel qu'écrit")
    date: Champ = Field(default_factory=Champ, description="date de signature ; valeur au format AAAA-MM-JJ, citation telle qu'écrite")
    autorite: Champ = Field(default_factory=Champ, description="signataire / service instructeur (préfet, DREAL, DDTM…)")
    procedure: Champ = Field(default_factory=Champ, description="nature de la décision : dérogation espèces protégées, autorisation environnementale, déclaration loi sur l'eau…")
    beneficiaire: Champ = Field(default_factory=Champ, description="pétitionnaire / maître d'ouvrage")
    projet: Champ = Field(default_factory=Champ, description="intitulé du projet autorisé")
    communes: list[str] = Field(default_factory=list)
    departement: Optional[str] = Field(None, description="code du département, ex. '33'")


class Duree(_IgnoreNull):
    duree_ans: Optional[int] = Field(None, description="durée des mesures de compensation / de suivi, en années")
    debut: Optional[int] = None
    fin: Optional[int] = None
    point_depart: Optional[str] = Field(None, description="tel qu'écrit, ex. « à compter de la notification »")
    citation: Optional[Citation] = None


TypeERC = Literal["E", "R", "C", "A", "inconnu"]


class MesureArrete(_IgnoreNull):
    code: str = Field(description="identifiant tel qu'écrit (« MC1 », « mesure C2 ») ou numéro d'ordre")
    libelle: str
    type_erc: TypeERC = "inconnu"
    cible: Optional[str] = Field(None, description="espèce, cortège ou milieu visé")
    surface_ha: Optional[float] = Field(None, description="surface imposée convertie en hectares")
    surface_texte: Optional[str] = Field(None, description="surface telle qu'écrite, ex. « 4 718 m² »")
    nb_parcelles: Optional[int] = None
    parcelles: list[str] = Field(default_factory=list)
    citation: Citation

    @field_validator("type_erc", mode="before")
    @classmethod
    def _t(cls, v):
        return _literal(TypeERC, "inconnu")(v)


class Evitement(_IgnoreNull):
    milieu: str
    surface_ha: Optional[float] = None
    citation: Citation


Categorie = Literal["suivi", "bilan_transmission", "travaux_gestion", "garantie_financiere",
                    "foncier", "geomce", "mesure_correctrice", "autre"]
FormeEcheance = Literal["ponctuel", "periodique", "liste_annees", "delai", "continu", "inconnu"]


class Echeancier(_IgnoreNull):
    forme: FormeEcheance = "inconnu"
    annees_relatives: list[int] = Field(default_factory=list, description="« années 1, 3, 5 » -> [1, 3, 5]")
    intervalle_ans: Optional[float] = None
    delai_mois: Optional[int] = Field(None, description="« sous 3 mois » -> 3")
    point_depart: Optional[str] = None
    texte: Optional[str] = Field(None, description="échéance telle qu'écrite")

    @field_validator("forme", mode="before")
    @classmethod
    def _f(cls, v):
        return _literal(FormeEcheance, "inconnu")(v)


class Prescription(_IgnoreNull):
    id: str = Field(description="P1, P2… dans l'ordre du document")
    article: Optional[str] = None
    categorie: Categorie = "autre"
    texte: str = Field(description="l'obligation en une phrase")
    mesures: list[str] = Field(default_factory=list, description="codes des mesures concernées")
    echeancier: Echeancier = Field(default_factory=Echeancier)
    destinataire: Optional[str] = Field(None, description="à qui transmettre (DREAL, DDTM, OFB…)")
    citation: Citation

    @field_validator("categorie", mode="before")
    @classmethod
    def _c(cls, v):
        return _literal(Categorie, "autre")(v)


class Terme(_IgnoreNull):
    terme: str
    sens: str


class FicheArrete(_IgnoreNull):
    identite: Identite = Field(default_factory=Identite)
    duree: Duree = Field(default_factory=Duree)
    mesures: list[MesureArrete] = Field(default_factory=list)
    evitement: list[Evitement] = Field(default_factory=list)
    prescriptions: list[Prescription] = Field(default_factory=list)
    termes: list[Terme] = Field(default_factory=list)
    resume: str = ""
    verification: dict = Field(default_factory=dict)     # rempli par verifier_arrete()


# ================================================================== prompt
PROMPT_ARRETE = """Tu lis une DÉCISION administrative (arrêté préfectoral, dérogation espèces
protégées, autorisation environnementale, déclaration loi sur l'eau…) qui impose des mesures
ERC à un maître d'ouvrage. Le texte est donné page par page (« === page N === »).

Renvoie :
- identite : référence, date (valeur AAAA-MM-JJ), autorité, nature de la procédure,
  bénéficiaire, intitulé du projet, communes, département ;
- duree : durée des mesures en années, années de début/fin si écrites, point de départ ;
- mesures : chaque mesure imposée (évitement, réduction, compensation, accompagnement) avec
  son identifiant tel qu'écrit, sa cible, sa surface (en ha + telle qu'écrite), le nombre et
  les références des parcelles ;
- evitement : les surfaces évitées par milieu ;
- prescriptions : CHAQUE obligation distincte (suivis, bilans et transmissions, travaux,
  garanties financières, maîtrise foncière, versement GéoMCE, mesures correctrices…) avec son
  échéancier (ponctuel, périodique, liste d'années relatives, délai en mois, continu) et son
  destinataire ;
- termes : le vocabulaire propre à la décision (identifiants de mesures, noms de sites) ;
- resume : 5 à 8 lignes pour un chargé d'affaires.

Règles impératives :
- Chaque citation est un extrait VERBATIM de 5 à 20 mots avec sa page exacte : elles sont
  vérifiées automatiquement, tout élément non retrouvé est ignoré.
- La citation d'une valeur chiffrée (référence, date, durée, surface) doit contenir cette valeur.
- N'invente rien. Laisse vide ce qui n'est pas écrit.
Réponds uniquement avec un JSON conforme au schéma fourni."""


# ================================================================== vérification
_NOMBRES = {"cinq": 5, "dix": 10, "quinze": 15, "vingt": 20, "vingt-cinq": 25, "trente": 30,
            "quarante": 40, "cinquante": 50, "soixante": 60, "quatre-vingt-dix-neuf": 99}


def _chiffres(s) -> list[str]:
    return re.findall(r"\d+", re.sub(r"(?<=\d)[\s\u202f.](?=\d{3}\b)", "", str(s or "")))


def _porte(valeur, citation: str, mode: str = "tous") -> bool:
    """La valeur figure-t-elle dans sa citation ? (chiffres, ou nombre écrit en lettres)"""
    cit = norm(citation)
    nums = _chiffres(valeur)
    if not nums:
        return True
    presents = [n in _chiffres(citation) for n in nums]
    if mode == "annee":
        return any(n in _chiffres(citation) for n in nums if len(n) == 4)
    if all(presents):
        return True
    return mode == "duree" and any(_NOMBRES.get(w) == int(nums[0]) for w in re.findall(r"[a-z\-]+", cit))


def verifier_arrete(fiche: FicheArrete, doc) -> FicheArrete:
    """Écarte tout élément dont la citation est introuvable (anti-hallucination)."""
    rap = {"identite": [0, 0], "mesures": [0, 0], "prescriptions": [0, 0], "evitement": [0, 0],
           "duree": [0, 0], "ecartes": []}

    def ok(c: Optional[Citation]) -> bool:
        if c is None:
            return False
        pg = _trouve(c.texte, doc, c.page, marge=1)
        if pg:
            c.page = pg
        return bool(pg)

    modes = {"reference": "tous", "date": "annee"}
    for nom in ("reference", "date", "autorite", "procedure", "beneficiaire", "projet"):
        ch: Champ = getattr(fiche.identite, nom)
        if ch.valeur is None:
            continue
        rap["identite"][1] += 1
        if ok(ch.citation) and (nom not in modes or _porte(ch.valeur, ch.citation.texte, modes[nom])):
            rap["identite"][0] += 1
        else:
            rap["ecartes"].append(f"identité.{nom} « {ch.valeur} »")
            setattr(fiche.identite, nom, Champ())

    d = fiche.duree
    if d.duree_ans or d.debut or d.fin:
        rap["duree"][1] = 1
        if ok(d.citation) and (not d.duree_ans or _porte(d.duree_ans, d.citation.texte, "duree")):
            rap["duree"][0] = 1
        else:
            rap["ecartes"].append(f"durée « {d.duree_ans} ans »")
            fiche.duree = Duree()

    def filtrer(liste, cle, test_valeur=lambda x: True):
        gardes = []
        for x in liste:
            rap[cle][1] += 1
            if ok(x.citation) and test_valeur(x):
                gardes.append(x)
                rap[cle][0] += 1
            else:
                rap["ecartes"].append(f"{cle[:-1]} « {getattr(x, 'code', None) or getattr(x, 'id', None) or getattr(x, 'milieu', '')} »")
        return gardes

    fiche.mesures = filtrer(fiche.mesures, "mesures",
                            lambda m: not m.surface_texte or _porte(m.surface_texte, m.citation.texte))
    fiche.evitement = filtrer(fiche.evitement, "evitement")
    fiche.prescriptions = filtrer(fiche.prescriptions, "prescriptions")
    fiche.verification = rap
    return fiche


# ================================================================== appel + cache
def _sous_doc(doc, pages: Optional[tuple[int, int]]):
    if not pages:
        return doc
    a, b = pages
    return doc.model_copy(update={"pages": [p for p in doc.pages if a <= p.num <= b]})


def charger_ou_lire_arrete(doc, cache_dir, pages: Optional[tuple[int, int]] = None) -> Optional[FicheArrete]:
    """Cache par sha256 (+ plage de pages pour une décision embarquée)."""
    from . import config, llm
    sd = _sous_doc(doc, pages)
    suffixe = f".p{pages[0]}-{pages[1]}" if pages else ""
    cache = cache_dir / f"{doc.sha256}{suffixe}.arrete.json"
    if cache.exists():
        return verifier_arrete(FicheArrete.model_validate_json(cache.read_text()), sd)
    if not llm.actif() or not sd.pages:
        return None
    texte = compacter(sd)
    t0 = time.time()
    schema = json.dumps(FicheArrete.model_json_schema(), ensure_ascii=False)
    fiche = llm._appel(PROMPT_ARRETE + "\n\nSchéma JSON :\n" + schema, texte, FicheArrete,
                       modele=config.modele_pour("arrete"), effort=config.effort_pour("arrete"),
                       max_tokens=config.max_tokens_pour("arrete"))
    fiche = verifier_arrete(fiche, sd)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(fiche.model_dump_json(indent=1))
    v = fiche.verification
    msg = (f"arrêté {doc.nom}{suffixe} : identité {v['identite'][0]}/{v['identite'][1]}, "
           f"mesures {v['mesures'][0]}/{v['mesures'][1]}, prescriptions {v['prescriptions'][0]}/"
           f"{v['prescriptions'][1]}, {len(v['ecartes'])} écart(s), {time.time() - t0:.1f} s")
    log.info(msg)
    print(f"📜 {msg}", flush=True)
    return fiche


# ================================================================== branchement en faits
def faits_depuis_fiche(fiche: FicheArrete, doc, cle: str = "projet") -> list[Fait]:
    """Mêmes types de faits que les regex de faits_arrete, methode="llm"."""
    out: list[Fait] = []

    def fait(type_, valeur, c: Optional[Citation], k: str = cle):
        src = Source(doc=doc.nom, loc=f"p.{c.page}" if c else "fiche arrêté",
                     extrait=c.texte[:120] if c else None)
        out.append(Fait(type=type_, cle=k, valeur=valeur, source=src, methode="llm",
                        confiance_extraction=0.8))

    I = fiche.identite
    for type_, ch in (("decision_reference", I.reference), ("type_procedure", I.procedure),
                      ("maitre_ouvrage", I.beneficiaire), ("projet_libelle", I.projet)):
        if ch.valeur:
            fait(type_, ch.valeur, ch.citation)
    if I.date.valeur and re.fullmatch(r"\d{4}-\d{2}-\d{2}", I.date.valeur):
        fait("decision_date", I.date.valeur, I.date.citation)

    d = fiche.duree
    if d.duree_ans:
        fait("duree_ans", int(d.duree_ans), d.citation)
    if d.debut or d.fin:
        fait("horizon", {"debut": d.debut, "fin": d.fin}, d.citation)

    for m in fiche.mesures:
        if m.type_erc == "C" and m.surface_ha:
            fait("obligation_surface", {"texte": " ".join(x for x in (m.libelle, m.cible) if x),
                                        "surface_min_ha": m.surface_ha, "nb_parcelles": m.nb_parcelles},
                 m.citation, k=m.code if cle == "projet" else f"{cle}:{m.code}")
    evit = [{"milieu": e.milieu, "surface_ha": e.surface_ha} for e in fiche.evitement if e.surface_ha]
    if evit:
        fait("evitement_surface", evit, fiche.evitement[0].citation)

    # la fiche complète, vérifiée : contexte de la passe 3 et source de arrete_prescription
    out.append(Fait(type="fiche_arrete", cle=cle,
                    valeur=fiche.model_dump(mode="json", exclude={"verification"}),
                    source=Source(doc=doc.nom, loc="fiche arrêté",
                                  extrait=f"{len(fiche.prescriptions)} prescriptions, {len(fiche.mesures)} mesures"),
                    methode="llm", confiance_extraction=0.8))
    return out