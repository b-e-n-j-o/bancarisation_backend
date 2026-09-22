"""Corrections BE → faits `methode='user'`, puis re-réconciliation.

Un code d'UG renommé est propagé dans les listes d'UG des actions avant
`reconcilier`, pour éviter une clé orpheline au passage suivant.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .modeles import CoucheProfil, Document, Fait, Source, ZoneCandidate
from .reconcile_ref import reconcilier

SRC = Source(doc="saisie BE", loc="validation")

PROJET_VERS_FAIT = {
    "nom": "projet_libelle",
    "maitre_ouvrage": "maitre_ouvrage",
    "reference_decision": "decision_reference",
    "date_decision": "decision_date",
    "type_procedure": "type_procedure",
    "commune": "commune",
    "duree_ans": "duree_ans",
}

ACTION_VERS_FAIT = {
    "intitule": "action_intitule",
    "ugs": "action_ug",
    "cible": "action_cible",
    "nature": "action_nature",
}

UG_VERS_FAIT = {
    "libelle": "ug_libelle",
    "type_erc": "ug_type_erc",
    "zone_sig": "ug_zone_sig",
    "surface_ha": "ug_surface",
}


def _coercer(type_: str, valeur: Any) -> Any:
    if type_ == "action_ug":
        if isinstance(valeur, str):
            from .excel_profil import ugs_dans
            return ugs_dans(valeur)
        return valeur or []
    if type_ == "ug_surface" and isinstance(valeur, str):
        try:
            return float(valeur.replace(",", "."))
        except ValueError:
            return valeur
    if type_ == "duree_ans" and isinstance(valeur, str):
        try:
            return int(valeur)
        except ValueError:
            return valeur
    return valeur


def _fait(type_: str, cle: str, valeur: Any) -> Fait:
    return Fait(type=type_, cle=cle, valeur=valeur, methode="user",
                confiance_extraction=1.0, source=SRC)


def _renommer_ugs(valeur: Any, renames: dict[str, str]) -> Any:
    if isinstance(valeur, list):
        return [renames.get(str(u), u) for u in valeur]
    return valeur


def faits_depuis_corrections(corrections: dict | None) -> tuple[list[Fait], dict[str, str], dict[str, str], set[str], set[str]]:
    """Retourne (faits user, renames UG, renames actions, UG retirées, actions retirées)."""
    if not corrections:
        return [], {}, {}, set(), set()
    extra: list[Fait] = []
    renames: dict[str, str] = {}
    act_renames: dict[str, str] = {}
    ug_suppr: set[str] = set()
    act_suppr: set[str] = set()

    for row in corrections.get("ugs") or []:
        ancien = str(row.get("id") or "")
        vals = dict(row.get("valeurs") or {})
        nouveau = str(vals.pop("code", None) or ancien).strip()
        if row.get("supprime"):
            ug_suppr.add(ancien)
            if nouveau:
                ug_suppr.add(nouveau)
            extra.append(_fait("ug_supprime", ancien, True))
            continue
        if not nouveau:
            continue
        if ancien and nouveau != ancien and row.get("origine") != "user":
            renames[ancien] = nouveau
        for cle, type_ in UG_VERS_FAIT.items():
            if cle in vals:
                extra.append(_fait(type_, nouveau, _coercer(type_, vals[cle])))
        if row.get("origine") == "user" and "libelle" not in vals:
            extra.append(_fait("ug_libelle", nouveau, vals.get("libelle") or nouveau))

    for row in corrections.get("actions") or []:
        ancien = str(row.get("id") or "")
        vals = dict(row.get("valeurs") or {})
        nouveau = str(vals.pop("code", None) or ancien).strip()
        if row.get("supprime"):
            act_suppr.add(ancien)
            if nouveau:
                act_suppr.add(nouveau)
            extra.append(_fait("action_supprime", ancien, True))
            continue
        if not nouveau:
            continue
        if ancien and nouveau != ancien:
            act_renames[ancien] = nouveau
        for cle, type_ in ACTION_VERS_FAIT.items():
            if cle in vals:
                extra.append(_fait(type_, nouveau, _coercer(type_, vals[cle])))
        if row.get("origine") == "user" and "intitule" not in vals:
            extra.append(_fait("action_intitule", nouveau, nouveau))

    for k, v in (corrections.get("projet") or {}).items():
        if k == "annee_fin":
            extra.append(_fait("horizon", "projet", {"fin": v} if not isinstance(v, dict) else v))
        elif k == "annee_etat_zero":
            extra.append(_fait("ancre_temporelle", "projet", {
                "etat_zero": int(v) if not isinstance(v, dict) else v.get("etat_zero", v),
                "N": None, "bloc": "user", "titre": "saisie BE", "nature": "user",
            }))
        elif k in PROJET_VERS_FAIT:
            extra.append(_fait(PROJET_VERS_FAIT[k], "projet", _coercer(PROJET_VERS_FAIT[k], v)))

    return extra, renames, act_renames, ug_suppr, act_suppr


def appliquer_aux_faits(faits: list[Fait], extra: list[Fait],
                        renames: dict[str, str], act_renames: dict[str, str],
                        ug_suppr: set[str], act_suppr: set[str]) -> list[Fait]:
    out: list[Fait] = []
    for f in faits:
        if f.methode == "user":
            continue
        cle = f.cle
        if f.type.startswith("ug_"):
            cle = renames.get(cle, cle)
        elif f.type.startswith("action_"):
            cle = act_renames.get(cle, cle)
        if f.type.startswith("ug_") and (f.cle in ug_suppr or cle in ug_suppr):
            continue
        if f.type.startswith("action_") and (f.cle in act_suppr or cle in act_suppr):
            continue
        val = f.valeur
        if f.type == "action_ug":
            val = [u for u in _renommer_ugs(val, renames) if u not in ug_suppr]
        if cle != f.cle or val != f.valeur:
            f = f.model_copy(update={"cle": cle, "valeur": val})
        out.append(f)
    return out + extra


def docs_depuis_sortie(sortie: dict) -> list[Document]:
    docs = []
    for d in sortie.get("documents") or []:
        docs.append(Document(
            nom=d.get("nom") or "",
            chemin="",
            sha256="",
            extension=Path(d.get("nom") or "").suffix,
            role=d.get("role") or "autre",
            role_indice=d.get("indice") or "",
            sous_documents=d.get("sous_documents") or [],
        ))
    return docs


def _hydrater_fait(d: dict | Fait) -> Fait:
    f = d if isinstance(d, Fait) else Fait(**d)
    if f.type == "coef_totaux" and isinstance(f.valeur, dict):
        val = dict(f.valeur)
        if isinstance(val.get("scores"), dict):
            val["scores"] = {float(k): v for k, v in val["scores"].items()}
        if val.get("k") is not None:
            try:
                val["k"] = float(val["k"])
            except (TypeError, ValueError):
                pass
        f = f.model_copy(update={"valeur": val})
    return f


def _socle_depuis_referentiel(sortie: dict, faits: list[Fait]) -> list[Fait]:
    """Reprise du nom déjà proposé si l'arrêté n'avait pas fourni de libellé
    et que les pages PDF ne sont plus en mémoire."""
    if any(f.type == "projet_libelle" for f in faits):
        return []
    prev = ((sortie.get("referentiel") or {}).get("projet") or {}).get("nom") or {}
    val = prev.get("valeur")
    if not isinstance(val, str) or not val.strip() or val.strip().startswith("!["):
        return []
    src = (prev.get("sources") or [{}])[0]
    return [Fait(
        type="projet_libelle", cle="projet", valeur=val, methode="deterministe",
        source=Source(doc=src.get("doc") or "referentiel", loc=src.get("loc") or "",
                      extrait=src.get("extrait")),
    )]


def appliquer_et_reconcilier(sortie: dict, corrections: dict | None) -> tuple[dict, list[dict]]:
    extra, renames, act_renames, ug_suppr, act_suppr = faits_depuis_corrections(corrections)
    faits = [_hydrater_fait(f) for f in sortie.get("faits") or []]
    faits += _socle_depuis_referentiel(sortie, faits)
    faits = appliquer_aux_faits(faits, extra, renames, act_renames, ug_suppr, act_suppr)
    zones = [ZoneCandidate(**z) for z in (sortie.get("sig") or {}).get("zones") or []]
    couches = [CoucheProfil(**c) for c in (sortie.get("sig") or {}).get("couches") or []]
    docs = docs_depuis_sortie(sortie)
    ref = reconcilier(faits, zones, couches, docs)
    return ref.model_dump(), [f.model_dump() for f in extra]
