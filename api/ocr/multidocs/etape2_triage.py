"""
etape2_triage.py — CARTOGRAPHIER le dossier, sans rien extraire.

Le modèle répond à trois questions et à trois questions seulement :
  1. quel(s) rôle(s) porte chaque document, et sur quelles plages de blocs ?
  2. quels sont les paramètres du dossier (T0, durée, périmètre) ?
  3. qu'est-ce qui a l'air d'être un doublon ou une contradiction entre fichiers ?

Il n'extrait AUCUNE action, AUCUN montant. C'est ce qui rend cette passe
bon marché (aperçus tronqués, pas les documents entiers) et fiable : on ne lui
demande pas de tenir deux tâches à la fois.

Un document = plusieurs segments possibles. Un plan de gestion porte très
souvent, en annexe, un calendrier récapitulatif et une estimation budgétaire :
un fichier, trois rôles, trois plages, trois extracteurs différents.

Garde-fou en sortie : tout segment référençant un doc_id ou un locator
inexistant est écarté (et journalisé). Le LLM n'a pas le droit d'inventer une
adresse.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .utils.corpus import Corpus
from .utils.familles import DESCRIPTION_ROLES, RoleDoc


class SegmentRole(BaseModel):
    doc_id: str
    role: RoleDoc
    locators: list[str] = Field(
        default_factory=list,
        description="Blocs couverts par ce rôle, tels qu'affichés entre ⟦ ⟧. "
                    "Liste vide = tout le document.",
    )
    description: str = Field(
        default="",
        description="Ce que contient réellement ce segment, en une phrase",
    )
    confiance: float = Field(ge=0, le=1)


class ParametreDetecte(BaseModel):
    cle: str = Field(
        description="annee_t0 | origine_t0 | duree_ans | perimetre | devise | maitre_ouvrage | bureau_etudes",
    )
    valeur: str
    ancre: Optional[str] = Field(None, description="doc_id#locator justifiant la valeur")
    confiance: float = Field(ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "cle" not in d and "nom" in d:
            d["cle"] = d["nom"]
        return d

    @field_validator("valeur", mode="before")
    @classmethod
    def _valeur_str(cls, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return str(v)


class SignalCorpus(BaseModel):
    type: str = Field(description="doublon | contradiction | manque | version_multiple")
    description: str
    doc_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"type": "manque", "description": data}
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "description" not in d and "message" in d:
            d["description"] = d["message"]
        d.setdefault("type", "manque")
        return d


class CarteDossier(BaseModel):
    segments: list[SegmentRole] = Field(default_factory=list)
    parametres: list[ParametreDetecte] = Field(default_factory=list)
    signaux: list[SignalCorpus] = Field(default_factory=list)
    documents_non_classes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _aplatir_documents(cls, data: Any) -> Any:
        """Le LLM renvoie parfois documents[{doc_id, segments:[…]}] au lieu
        du schéma plat segments[{doc_id, role, …}]."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        segs = list(d.get("segments") or [])
        if not segs and d.get("documents"):
            for doc in d["documents"]:
                if not isinstance(doc, dict):
                    continue
                doc_id = doc.get("doc_id") or ""
                for s in doc.get("segments") or []:
                    if not isinstance(s, dict):
                        continue
                    flat = dict(s)
                    flat.setdefault("doc_id", doc_id)
                    flat.setdefault("description", flat.get("role", ""))
                    segs.append(flat)
            d["segments"] = segs
        return d

    def parametres_dict(self, seuil: float = 0.5) -> dict:
        return {p.cle: p.valeur for p in self.parametres if p.confiance >= seuil}


def _catalogue_roles() -> str:
    return "\n".join(f"- {r.value} : {d}" for r, d in DESCRIPTION_ROLES.items())


SYSTEM_PROMPT = f"""Tu cartographies un dossier de compensation écologique déposé par un bureau \
d'études français. Le dossier est fourni sous forme d'APERÇUS : pour chaque document, une liste \
de blocs adressables. Chaque bloc est précédé de son adresse entre ⟦ ⟧ (⟦p12⟧ pour la page 12 \
d'un PDF, ⟦Planning!8:40⟧ pour les lignes 8 à 40 de la feuille Planning d'un classeur, ⟦§3⟧ pour \
une section). Les aperçus sont TRONQUÉS : tu vois le début de chaque bloc, pas son contenu complet.

TA TÂCHE EST EXCLUSIVEMENT DE CARTOGRAPHIER. Tu n'extrais aucune action, aucune date, aucun \
montant. Une autre passe s'en chargera, guidée par ta carte.

RÔLES DOCUMENTAIRES POSSIBLES :
{_catalogue_roles()}

RÈGLES :
1. Un même fichier peut porter PLUSIEURS rôles sur des plages différentes. C'est le cas le plus \
fréquent : un plan de gestion contient souvent, en annexe, un calendrier récapitulatif \
(calendrier_previsionnel) et parfois une estimation (estimation_budget). Un classeur d'estimation \
contient souvent une feuille de coûts unitaires (estimation_budget), une matrice actions × années \
(planning_financier), un décompte (decompte_facturation) et un état d'avancement (statut_realisation). \
Découpe : un segment par rôle, avec ses locators.
2. N'invente jamais un doc_id ni un locator. Dans le JSON, les locators sont \
SANS les crochets d'affichage : écris "p12" (pas "⟦p12⟧"), "Planning!8:40" (pas \
"⟦Planning!8:40⟧"). Reprends exactement l'adresse vue entre ⟦ ⟧, sans les ⟦ ⟧.
3. Si un segment couvre tout le document (cas typique d'un plan de gestion PDF), \
laisse locators vide [] plutôt que de lister quelques pages d'aperçu.
4. Un document que tu n'arrives pas à classer va dans documents_non_classes. Ne force pas un rôle : \
un rôle faux coûte plus cher qu'un rôle absent, parce qu'il déclenche le mauvais extracteur.
5. PARAMÈTRES : cherche l'année de départ de la compensation (annee_t0), ce qui la déclenche \
(origine_t0 : signature de l'arrêté, réception des travaux, état initial…), la durée d'engagement \
(duree_ans, souvent 30), le maître d'ouvrage, le bureau d'études. Chaque paramètre doit porter \
l'ancre qui le justifie. Si une valeur n'est pas explicite, ne la devine pas. \
Champs EXACTS : cle (pas "nom"), valeur (toujours une chaîne), ancre, confiance.
6. SIGNAUX : signale les doublons (deux versions d'un même plan), les contradictions apparentes \
entre fichiers, les manques évidents (un budget qui référence des actions qu'aucun document ne \
décrit). Tu signales, tu n'arbitres pas. \
Champs EXACTS par signal : type, description (pas "message"), doc_ids (liste).
7. La confiance est une vraie estimation, pas une politesse. Sous 0.5, le segment sera soumis à \
validation humaine avant extraction.
8. FORMAT DE SORTIE — segments PLATS (pas de documents[].segments) :
{{"segments":[{{"doc_id":"…","role":"plan_gestion","locators":[],"description":"…","confiance":0.9}}],\
"parametres":[{{"cle":"annee_t0","valeur":"2022","ancre":"doc#p1","confiance":0.8}}],\
"signaux":[{{"type":"manque","description":"…","doc_ids":[]}}],\
"documents_non_classes":[]}}"""


USER_PROMPT = """Voici les aperçus des documents du dossier. Cartographie-le.

<dossier>
{apercus}
</dossier>"""


def cartographier(
    corpus: Corpus,
    *,
    model: Optional[str] = None,
    effort: str = "none",
    compteur=None,
    debug_dir=None,
    max_tokens: int = 8000,
) -> CarteDossier:
    """Appel LLM de triage, puis validation déterministe des adresses.

    Classification de rôles documentaires : modèle petit + effort none
    (mistral-small-2603 n'accepte que none|high).
    """
    from ..mistral_client import extraire_structure

    # mistral-small-2603 (Small 4) — assez pour segmenter / typer les docs
    modele_triage = model or "mistral-small-2603"

    carte: CarteDossier = extraire_structure(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=USER_PROMPT.format(apercus=corpus.apercu()),
        result_type=CarteDossier,
        etiquettes="TRIAGE",
        debug_dir=debug_dir,
        debug_prefixe="triage",
        model=modele_triage,
        effort=effort,
        max_tokens=max_tokens,
        utiliser_schema=False,
        compteur=compteur,
        schema_name="carte_dossier",
    )
    carte = valider_carte(carte, corpus)
    if corpus.documents and not carte.segments:
        raise ValueError(
            "Triage : aucun segment cartographié. Relancer l'analyse "
            "(le modèle a renvoyé une carte vide ou un schéma incorrect)."
        )
    return carte


def _normaliser_locator(loc: str) -> str:
    """Le LLM recopié parfois ⟦p12⟧ au lieu de p12 — on retire le décor d'aperçu."""
    s = (loc or "").strip()
    if s.startswith("⟦") and s.endswith("⟧") and len(s) >= 2:
        s = s[1:-1].strip()
    return s


def valider_carte(carte: CarteDossier, corpus: Corpus) -> CarteDossier:
    """Écarte tout ce qui pointe vers une adresse inexistante. Un LLM qui
    hallucine une page ne doit jamais faire tomber un job dans le vide."""
    from .utils.familles import RoleDoc

    segments_ok: list[SegmentRole] = []
    for seg in carte.segments:
        doc = corpus.doc(seg.doc_id)
        if doc is None:
            carte.signaux.append(SignalCorpus(
                type="manque",
                description=f"Segment écarté : doc_id inconnu '{seg.doc_id}'",
            ))
            continue
        connus = {b.locator for b in doc.blocs}
        bruts = [_normaliser_locator(l) for l in seg.locators]
        bruts = [l for l in bruts if l]
        valides = [l for l in bruts if l in connus]
        perdus = [l for l in bruts if l not in connus]
        if perdus:
            carte.signaux.append(SignalCorpus(
                type="manque",
                description=f"{len(perdus)} locator(s) inconnu(s) écarté(s) sur {seg.doc_id} : "
                            f"{', '.join(perdus[:5])}",
                doc_ids=[seg.doc_id],
            ))
        if bruts and not valides:
            continue  # segment entièrement halluciné

        # Plan / fiches sur un long PDF : quelques pages d'aperçu ≠ plage utile.
        # On bascule sur document entier (locators []) pour ne pas tronquer l'extraction.
        if (
            seg.role in (RoleDoc.plan_gestion, RoleDoc.fiches_actions)
            and doc.format == "pdf"
            and 0 < len(valides) <= 3
            and len(doc.blocs) > 10
        ):
            carte.signaux.append(SignalCorpus(
                type="manque",
                description=(
                    f"{seg.doc_id} / {seg.role.value} : {len(valides)} locator(s) "
                    f"trop partiel(s) pour un PDF de {len(doc.blocs)} pages → "
                    f"document entier retenu"
                ),
                doc_ids=[seg.doc_id],
            ))
            valides = []

        seg.locators = valides
        segments_ok.append(seg)

    carte.segments = segments_ok

    couverts = {s.doc_id for s in carte.segments}
    carte.documents_non_classes = sorted(
        {d.doc_id for d in corpus.documents} - couverts
    )
    return carte