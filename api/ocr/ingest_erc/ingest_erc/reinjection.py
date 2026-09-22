"""Étape 2.5 — réinjection des réponses et corrections du BE, puis verrouillage.

Cycle :
    vue = reconcilier(faits, ...)                        # questions + tables
    faits_user = faits_depuis_validation(reponses, corrections)
    faits_user = fusionner_faits_user(anciens_user, faits_user)   # à persister
    vue = reconcilier(faits + faits_user, ...)           # instantané, déterministe
    si aucune question bloquante : ref = verrouiller(vue)

Les faits user sont PERSISTÉS (fichier du run ou table) : si un document est ajouté
plus tard et que la passe 1 est rejouée, les décisions du BE sont conservées.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .modeles import (ActionVerrouillee, Fait, ReferentielPropose, ReferentielVerrouille,
                      Source, UGVerrouillee)

TYPES_REINJECTION = {"reponse", "commentaire", "correction", "renommage",
                     "ug_ajout", "action_ajout", "ug_supprime", "action_supprime"}

# Réponse libre : la question est close, AUCUNE valeur n'est écrite (la proposition du
# système reste en place avec sa confiance), le texte devient un commentaire transmis
# à la passe 3. Pour changer une valeur, le BE corrige la cellule dans le tableau.
LIBRE = "__libre__"


def _src(loc: str, extrait: Any = None) -> Source:
    return Source(doc="BE", loc=loc, extrait=None if extrait is None else str(extrait)[:120])


def _fait(type_: str, cle: str, valeur: Any, loc: str) -> Fait:
    return Fait(type=type_, cle=cle, valeur=valeur, source=_src(loc, valeur), methode="user")


# ------------------------------------------------------------------ écran -> faits
def faits_depuis_validation(reponses: dict[str, str], corrections: dict,
                            questions: list | None = None) -> list[Fait]:
    """Traduit exactement ce qu'envoie ValidationEtape.tsx (onValider).

    `questions` = les questions AFFICHÉES au BE (referentiel.questions du run) : une réponse
    qui n'est pas une de leurs options est une réponse libre -> commentaire.
    """
    options = None if questions is None else \
        {q.id: set(q.options) | ({q.proposition} if q.proposition else set()) for q in questions}
    out: list[Fait] = []
    for qid, v in (reponses or {}).items():
        if v in (None, "", "__autre"):
            continue
        if options is None or v in options.get(qid, set()):
            out.append(_fait("reponse", qid, v, f"question {qid}"))
        else:
            out.append(_fait("reponse", qid, LIBRE, f"question {qid}"))
            out.append(_fait("commentaire", qid, str(v).strip(), f"question {qid}"))

    for champ, v in (corrections.get("projet") or {}).items():
        out.append(_fait("correction", f"projet.{champ}", v, f"projet · {champ}"))

    for table, cle_json in (("ug", "ugs"), ("action", "actions")):
        for r in corrections.get(cle_json) or []:
            vals = dict(r.get("valeurs") or {})
            nouveau = str(vals.pop("code", "") or "").strip()
            if r.get("origine") == "user":                    # ligne ajoutée à la main
                if r.get("supprime") or not nouveau:
                    continue
                code = nouveau
                out.append(_fait(f"{table}_ajout", code, None, f"{table} ajoutée"))
            else:
                code = r["id"]
                if r.get("supprime"):
                    out.append(_fait(f"{table}_supprime", code, None, f"{table} retirée"))
                    continue
                if nouveau and nouveau != code:
                    out.append(_fait("renommage", f"{table}.{code}", nouveau, f"{table} renommée"))
                    code = nouveau
            for champ, v in vals.items():
                out.append(_fait("correction", f"{table}.{code}.{champ}", v, f"{table} {code} · {champ}"))
    return out


def fusionner_faits_user(anciens: list[Fait], nouveaux: list[Fait]) -> list[Fait]:
    """Dernière saisie gagnante par (type, cle). Une ligne ré-ajoutée annule sa suppression."""
    par_cle: dict[tuple, Fait] = {(f.type, f.cle): f for f in anciens}
    for f in nouveaux:
        par_cle[(f.type, f.cle)] = f
        oppose = {"ug_ajout": "ug_supprime", "ug_supprime": "ug_ajout",
                  "action_ajout": "action_supprime", "action_supprime": "action_ajout"}.get(f.type)
        if oppose:
            par_cle.pop((oppose, f.cle), None)
    return list(par_cle.values())


# ------------------------------------------------------------------ renommages
def appliquer_renommages(faits: list[Fait], zones: list) -> tuple[list[Fait], list]:
    """Propage chaque renommage de code dans TOUS les faits (clés, listes d'UG, dicts)
    et dans les attributs UG des zones SIG. Les id de questions répondues suivent aussi.

    Hypothèse : un ancien code d'UG n'est pas identique à un code d'action.
    """
    ren = {f.cle.split(".", 1)[1]: f.valeur for f in faits if f.type == "renommage"}
    if not ren:
        return faits, zones

    def sub(v):
        if isinstance(v, str):
            return ren.get(v, v)
        if isinstance(v, list):
            return [sub(x) for x in v]
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        return v

    out = []
    for f in faits:
        if f.type in ("renommage", "correction"):
            out.append(f)                        # déjà exprimés avec les nouveaux codes
        elif f.type in ("reponse", "commentaire"):
            regle, _, portee = f.cle.partition(":")
            portee = "+".join(ren.get(p, p) for p in portee.split("+"))
            out.append(f.model_copy(update={"cle": f"{regle}:{portee}"}))
        else:
            out.append(f.model_copy(update={"cle": ren.get(f.cle, f.cle), "valeur": sub(f.valeur)}))
    zones2 = [z.model_copy(update={"ug_attribut": ren.get(z.ug_attribut, z.ug_attribut)})
              if getattr(z, "ug_attribut", None) else z for z in zones]
    return out, zones2


# ------------------------------------------------------------------ verrouillage
class QuestionsOuvertes(ValueError):
    def __init__(self, ids: list[str]):
        super().__init__(f"{len(ids)} question(s) bloquante(s) ouverte(s) : {', '.join(ids)}")
        self.ids = ids


def verrouiller(ref: ReferentielPropose, force: bool = False) -> ReferentielVerrouille:
    ouvertes = [q.id for q in ref.questions if q.bloquante]
    if ouvertes and not force:
        raise QuestionsOuvertes(ouvertes)

    provenance: dict[str, dict] = {}

    def v(chemin: str, c):
        if c is None:
            return None
        provenance[chemin] = {
            "origine": "be" if any(s.doc == "BE" for s in c.sources) else "accepte",
            "confiance": c.confiance,
            "sources": [s.model_dump() for s in c.sources if s.doc != "BE"][:6],
        }
        return c.valeur

    projet = {k: v(f"projet.{k}", c) for k, c in ref.projet.items()}
    ugs = [UGVerrouillee(
        ug_code=u.ug_code,
        libelle=v(f"ug.{u.ug_code}.libelle", u.libelle),
        type_erc=v(f"ug.{u.ug_code}.type_erc", u.type_erc),
        zone_sig=v(f"ug.{u.ug_code}.zone_sig", u.zone_sig),
        surface_ha=v(f"ug.{u.ug_code}.surface_ha", u.surface_ha),
    ) for u in ref.ugs]
    actions = [ActionVerrouillee(
        code=a.code,
        intitule=v(f"action.{a.code}.intitule", a.intitule),
        nature=v(f"action.{a.code}.nature", a.nature),
        ugs=list(v(f"action.{a.code}.ugs", a.ugs) or []),
        cible=v(f"action.{a.code}.cible", a.cible),
    ) for a in ref.actions]

    corps = {"projet": projet,
             "ugs": [u.model_dump() for u in ugs],
             "actions": [a.model_dump() for a in actions],
             "obligations_surfaciques": ref.obligations_surfaciques,
             "decisions": ref.decisions}
    empreinte = hashlib.sha256(
        json.dumps(corps, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

    return ReferentielVerrouille(
        projet=projet, ugs=ugs, actions=actions,
        obligations_surfaciques=ref.obligations_surfaciques, decisions=ref.decisions,
        provenance=provenance, questions_forcees=ouvertes if force else [],
        empreinte=empreinte, verrouille_le=datetime.now(timezone.utc).isoformat(),
    )