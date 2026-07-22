"""
crud.py — CRUD post-ingestion sur le schéma bancarisation.

Accès via API REST Supabase (SUPABASE_URL HTTPS), pas Postgres direct.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from api.db.supabase import bancarisation, get_supabase
from api.ocr.domain.ug_ids import normalize_ug_id, normalize_ug_ids

_CHAMPS_MODIFIABLES = {
    "annee", "code", "titre", "categorie", "lib_thema", "statut", "ug_ids",
    "mois_debut", "mois_fin", "traverse_nouvel_an",
    "date_realisation", "commentaire",
    "montant_ht", "montant_ttc", "taux_tva", "prestataire", "prestataire_id",
    "ligne_budget_id",
    "montant_engage", "montant_realise",
}

_CHAMPS_ACTION_MODIFIABLES = {"ug_ids", "titre", "contenu_integral", "categorie", "lib_thema"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid(projet_id: UUID | str) -> str:
    return str(projet_id)


def _oid(occurrence_id: UUID | str) -> str:
    return str(occurrence_id)


# --- Lecture -----------------------------------------------------------------


def get_metadata(projet_id: UUID | str) -> dict[str, Any] | None:
    client = get_supabase()
    response = (
        bancarisation(client)
        .table("projet_metadata")
        .select("*")
        .eq("projet_id", _pid(projet_id))
        .maybe_single()
        .execute()
    )
    return response.data


def lister_actions(projet_id: UUID | str) -> list[dict[str, Any]]:
    client = get_supabase()
    response = (
        bancarisation(client)
        .table("action_fiche")
        .select(
            "id, cle, code, categorie, titre, contenu_integral, ug_ids, lib_thema, confiance, "
            "champs_a_confirmer, avertissements"
        )
        .eq("projet_id", _pid(projet_id))
        .order("code")
        .execute()
    )
    return response.data or []


def lister_actions_pour_ug(
    projet_id: UUID | str,
    ug_id: str,
) -> list[dict[str, Any]]:
    """Actions liées à une UG + nombre d'occurrences sur cette UG."""
    ug = normalize_ug_id(ug_id)
    if not ug:
        raise ValueError("ug_id invalide.")

    pid = _pid(projet_id)
    actions = lister_actions(pid)
    occs = lister_occurrences(pid, ug_id=ug, inclure_supprimees=False)

    counts: dict[str, int] = {}
    for o in occs:
        cle = o.get("action_cle")
        if cle:
            counts[str(cle)] = counts.get(str(cle), 0) + 1

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in actions:
        cle = str(a.get("cle") or "")
        action_ugs = normalize_ug_ids(a.get("ug_ids") or [])
        linked = ug in action_ugs or cle in counts
        if not linked:
            continue
        row = dict(a)
        row["ug_ids"] = action_ugs
        row["nb_occurrences"] = counts.get(cle, 0)
        out.append(row)
        seen.add(cle)

    # Actions absentes de la liste (rare) mais présentes via occurrences
    for cle, n in counts.items():
        if cle in seen:
            continue
        out.append({
            "id": None,
            "cle": cle,
            "code": cle,
            "categorie": "",
            "titre": cle,
            "contenu_integral": "",
            "ug_ids": [ug],
            "nb_occurrences": n,
        })

    out.sort(key=lambda r: (str(r.get("code") or ""), str(r.get("cle") or "")))
    return out


def _normaliser_code_action(code: str) -> str:
    return code.replace(" ", "").strip().upper()


_CATEGORIES_ACTION = frozenset({"TU", "TE", "SE", "MG", "EP"})


def creer_action_fiche(
    projet_id: UUID | str,
    *,
    code: str,
    categorie: str,
    titre: str,
    contenu_integral: str,
    cle: str | None = None,
    ug_ids: list[str] | None = None,
    lib_thema: str | None = None,
) -> dict[str, Any]:
    """Crée une fiche-action saisie manuellement (hors import OCR)."""
    from api.ocr.extractions.catalogue.thema import normaliser_lib_thema

    code_norm = _normaliser_code_action(code)
    cat = categorie.strip().upper()
    if cat not in _CATEGORIES_ACTION:
        raise ValueError(
            f"Catégorie invalide : {categorie}. Attendu : {sorted(_CATEGORIES_ACTION)}"
        )
    titre_clean = titre.strip()
    contenu_clean = contenu_integral.strip()
    if not titre_clean:
        raise ValueError("Le titre est obligatoire.")
    if not contenu_clean:
        raise ValueError("Le contenu est obligatoire.")

    cle_norm = _normaliser_code_action(cle or code_norm)
    ugs = normalize_ug_ids(ug_ids)
    thema = normaliser_lib_thema(lib_thema)
    client = get_supabase()

    existing = (
        bancarisation(client)
        .table("action_fiche")
        .select("id")
        .eq("projet_id", _pid(projet_id))
        .eq("cle", cle_norm)
        .limit(1)
        .execute()
    )
    if existing.data:
        raise ValueError(f"Une fiche avec le code {cle_norm} existe déjà sur ce projet.")

    fiche_json = {
        "id": cle_norm,
        "code": code_norm,
        "categorie": cat,
        "titre": titre_clean,
        "lib_thema": thema,
        "contenu_integral": contenu_clean,
        "ug_ids": ugs,
        "confiance": 1.0,
        "champs_a_confirmer": [],
        "avertissements": ["Fiche créée manuellement"],
    }

    response = (
        bancarisation(client)
        .table("action_fiche")
        .insert({
            "projet_id": _pid(projet_id),
            "cle": cle_norm,
            "code": code_norm,
            "categorie": cat,
            "titre": titre_clean,
            "contenu_integral": contenu_clean,
            "fiche_json": fiche_json,
            "ug_ids": ugs,
            "lib_thema": thema,
            "confiance": 1.0,
            "champs_a_confirmer": [],
            "avertissements": ["Fiche créée manuellement"],
        })
        .execute()
    )
    data = response.data
    if not data:
        raise RuntimeError("Insertion fiche-action échouée.")
    return data[0] if isinstance(data, list) else data


def modifier_action_fiche(
    projet_id: UUID | str,
    action_id: UUID | str,
    **champs: Any,
) -> dict[str, Any] | None:
    from api.ocr.extractions.catalogue.thema import normaliser_lib_thema

    maj = {k: v for k, v in champs.items() if k in _CHAMPS_ACTION_MODIFIABLES}
    if not maj:
        raise ValueError(
            f"Aucun champ modifiable. Autorisés : {sorted(_CHAMPS_ACTION_MODIFIABLES)}"
        )
    if "ug_ids" in maj:
        maj["ug_ids"] = normalize_ug_ids(maj["ug_ids"])
    if "lib_thema" in maj:
        maj["lib_thema"] = normaliser_lib_thema(maj["lib_thema"])

    client = get_supabase()
    response = (
        bancarisation(client)
        .table("action_fiche")
        .update(maj, returning="representation")
        .eq("id", str(action_id))
        .eq("projet_id", _pid(projet_id))
        .execute()
    )
    data = response.data
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


def lister_echeances(projet_id: UUID | str) -> list[dict[str, Any]]:
    client = get_supabase()
    response = (
        bancarisation(client)
        .table("echeance")
        .select(
            "id, cle, action_cle, code_operation, libelle, confiance, "
            "champs_a_confirmer, avertissements, source_page, ug_ids"
        )
        .eq("projet_id", _pid(projet_id))
        .order("code_operation")
        .execute()
    )
    return response.data or []


_ECHEANCE_SELECT = (
    "id, cle, action_cle, code_operation, type_operation, type_metier, libelle, "
    "recurrence, confiance, champs_a_confirmer, avertissements, source_page, "
    "ug_ids, fenetre_debut, fenetre_fin, fenetre_traverse_nouvel_an"
)


def _est_a_revoir(row: dict[str, Any]) -> bool:
    confiance = float(row.get("confiance") or 0)
    champs = row.get("champs_a_confirmer") or []
    avert = row.get("avertissements") or []
    return confiance < 0.7 or len(champs) > 0 or len(avert) > 0


def _est_non_placable(row: dict[str, Any]) -> bool:
    rec = row.get("recurrence") or {}
    if rec.get("type") == "dependant_evenement" and not rec.get("ancrage_annee"):
        return True
    return _est_a_revoir(row)


def _lister_echeances_detail(projet_id: UUID | str) -> list[dict[str, Any]]:
    client = get_supabase()
    response = (
        bancarisation(client)
        .table("echeance")
        .select(_ECHEANCE_SELECT)
        .eq("projet_id", _pid(projet_id))
        .order("code_operation")
        .execute()
    )
    return response.data or []


def echeances_non_placables(projet_id: UUID | str) -> list[dict[str, Any]]:
    """Échéances sans occurrence en base — à positionner manuellement sur le calendrier."""
    echeances = _lister_echeances_detail(projet_id)
    occs = lister_occurrences(projet_id)
    placees = {str(o["echeance_id"]) for o in occs if o.get("echeance_id")}
    return [
        e for e in echeances
        if str(e.get("id")) not in placees and _est_non_placable(e)
    ]


def echeances_a_revoir(projet_id: UUID | str) -> list[dict[str, Any]]:
    return [e for e in _lister_echeances_detail(projet_id) if _est_a_revoir(e)]


def lister_occurrences(
    projet_id: UUID | str,
    *,
    annee: int | None = None,
    ug_id: str | None = None,
    inclure_supprimees: bool = True,
) -> list[dict[str, Any]]:
    """Alimente le calendrier via la vue v_occurrence_calendrier."""
    client = get_supabase()
    query = (
        bancarisation(client)
        .table("v_occurrence_calendrier")
        .select("*")
        .eq("projet_id", _pid(projet_id))
    )
    if annee is not None:
        query = query.eq("annee", annee)
    if ug_id is not None:
        ug_norm = normalize_ug_id(ug_id)
        if ug_norm:
            query = query.contains("ug_ids", [ug_norm])
    if not inclure_supprimees:
        query = query.neq("statut", "supprime")

    response = query.order("annee").order("code").execute()
    return response.data or []


# --- CRUD occurrences --------------------------------------------------------


def creer_occurrence(projet_id: UUID | str, **champs: Any) -> dict[str, Any]:
    colonnes = {
        k: v for k, v in champs.items()
        if k in _CHAMPS_MODIFIABLES or k == "echeance_id"
    }
    if colonnes.get("echeance_id") is not None:
        colonnes["echeance_id"] = str(colonnes["echeance_id"])
    if "ug_ids" in colonnes:
        colonnes["ug_ids"] = normalize_ug_ids(colonnes["ug_ids"])
    colonnes["projet_id"] = _pid(projet_id)
    colonnes["origine"] = "user"

    client = get_supabase()
    response = (
        bancarisation(client)
        .table("occurrence")
        .insert(colonnes)
        .execute()
    )
    data = response.data
    if not data:
        raise RuntimeError("Insertion occurrence échouée.")
    return data[0] if isinstance(data, list) else data


def modifier_occurrence(
    occurrence_id: UUID | str,
    **champs: Any,
) -> dict[str, Any] | None:
    maj = {k: v for k, v in champs.items() if k in _CHAMPS_MODIFIABLES}
    if not maj:
        raise ValueError(f"Aucun champ modifiable. Autorisés : {sorted(_CHAMPS_MODIFIABLES)}")
    if "ug_ids" in maj:
        maj["ug_ids"] = normalize_ug_ids(maj["ug_ids"])

    maj["modifie_le"] = _now_iso()
    client = get_supabase()
    response = (
        bancarisation(client)
        .table("occurrence")
        .update(maj, returning="representation")
        .eq("id", _oid(occurrence_id))
        .execute()
    )
    data = response.data
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


def supprimer_occurrence(
    occurrence_id: UUID | str,
    *,
    definitif: bool = False,
) -> bool:
    client = get_supabase()
    if definitif:
        response = (
            bancarisation(client)
            .table("occurrence")
            .delete()
            .eq("id", _oid(occurrence_id))
            .execute()
        )
        data = response.data
        return bool(data)
    response = (
        bancarisation(client)
        .table("occurrence")
        .update({"statut": "supprime", "modifie_le": _now_iso()}, returning="representation")
        .eq("id", _oid(occurrence_id))
        .execute()
    )
    return bool(response.data)
