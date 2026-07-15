"""
gestion_crud.py — CRUD post-ingestion sur le schéma bancarisation.

Accès via API REST Supabase (SUPABASE_URL HTTPS), pas Postgres direct.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .supabase_client import bancarisation, get_supabase

_CHAMPS_MODIFIABLES = {
    "annee", "code", "titre", "categorie", "statut", "ug_ids",
    "mois_debut", "mois_fin", "traverse_nouvel_an",
    "date_realisation", "commentaire",
}


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
            "id, cle, code, categorie, titre, contenu_integral, confiance, "
            "champs_a_confirmer, avertissements"
        )
        .eq("projet_id", _pid(projet_id))
        .order("code")
        .execute()
    )
    return response.data or []


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
) -> dict[str, Any]:
    """Crée une fiche-action saisie manuellement (hors import OCR)."""
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
        "contenu_integral": contenu_clean,
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


def lister_echeances(projet_id: UUID | str) -> list[dict[str, Any]]:
    client = get_supabase()
    response = (
        bancarisation(client)
        .table("echeance")
        .select(
            "id, cle, action_cle, code_operation, libelle, confiance, "
            "champs_a_confirmer, avertissements, source_page"
        )
        .eq("projet_id", _pid(projet_id))
        .order("code_operation")
        .execute()
    )
    return response.data or []


_ECHEANCE_SELECT = (
    "id, cle, action_cle, code_operation, type_operation, type_metier, libelle, "
    "recurrence, confiance, champs_a_confirmer, avertissements, source_page, "
    "unites_gestion, fenetre_debut, fenetre_fin, fenetre_traverse_nouvel_an"
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
        query = query.contains("ug_ids", [ug_id])
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
