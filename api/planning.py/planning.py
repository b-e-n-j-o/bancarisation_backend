import os
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

ORGANISATION_ID_V0 = "a1000000-0000-0000-0000-000000000001"


class PlanningCrudError(Exception):
    pass


def _get_supabase_client() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_key:
        raise PlanningCrudError(
            "Variables SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY manquantes."
        )
    return create_client(supabase_url, service_key)


# =============================================================================
# ACTIONS (comp_affectation_annuelle)
# =============================================================================

CATEGORIES_VALIDES = {"MG", "SE", "TU", "TE"}
STATUTS_VALIDES = {"projete", "realise", "supprime", "conditionnel"}


@dataclass
class CreateActionPayload:
    projet_id: UUID
    annee: int
    categorie: str                        # MG | SE | TU | TE
    libelle_prestation: str
    statut: str = "projete"
    cout_ht_prevu: Optional[float] = None
    prestataire_id: Optional[UUID] = None
    unit_id: Optional[UUID] = None        # nullable si action transversale
    note: Optional[str] = None


@dataclass
class UpdateActionPayload:
    annee: Optional[int] = None
    categorie: Optional[str] = None
    libelle_prestation: Optional[str] = None
    statut: Optional[str] = None
    cout_ht_prevu: Optional[float] = None
    prestataire_id: Optional[UUID] = None
    unit_id: Optional[UUID] = None
    note: Optional[str] = None


def _validate_categorie(cat: str) -> None:
    if cat not in CATEGORIES_VALIDES:
        raise PlanningCrudError(
            f"Catégorie invalide '{cat}'. Valeurs acceptées : {CATEGORIES_VALIDES}"
        )


def _validate_statut(statut: str) -> None:
    if statut not in STATUTS_VALIDES:
        raise PlanningCrudError(
            f"Statut invalide '{statut}'. Valeurs acceptées : {STATUTS_VALIDES}"
        )


def lister_actions(projet_id: UUID) -> list[dict[str, Any]]:
    """Retourne toutes les actions d'un projet, enrichies avec prestataire et unité."""
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("comp_affectation_annuelle")
            .select(
                "*, "
                "comp_prestataire(id, nom, role_defaut), "
                "comp_gestion_unit(id, code, libelle, type_milieu)"
            )
            .eq("projet_id", str(projet_id))
            .order("annee")
            .order("categorie")
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    return response.data or []


def lire_action(action_id: UUID) -> dict[str, Any]:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("comp_affectation_annuelle")
            .select(
                "*, "
                "comp_prestataire(id, nom, role_defaut), "
                "comp_gestion_unit(id, code, libelle, type_milieu)"
            )
            .eq("id", str(action_id))
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    row = response.data
    if not row:
        raise PlanningCrudError("Action introuvable.")
    return row


def creer_action(payload: CreateActionPayload) -> UUID:
    _validate_categorie(payload.categorie)
    _validate_statut(payload.statut)

    client = _get_supabase_client()
    insert_payload: dict[str, Any] = {
        "projet_id": str(payload.projet_id),
        "annee": payload.annee,
        "categorie": payload.categorie,
        "libelle_prestation": payload.libelle_prestation.strip(),
        "statut": payload.statut,
        "cout_ht_prevu": payload.cout_ht_prevu,
        "prestataire_id": str(payload.prestataire_id) if payload.prestataire_id else None,
        "unit_id": str(payload.unit_id) if payload.unit_id else None,
        "note": payload.note,
    }

    try:
        response = (
            client.schema("bancarisation")
            .table("comp_affectation_annuelle")
            .insert(insert_payload, returning="representation")
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row or "id" not in row:
        raise PlanningCrudError("Insertion échouée : identifiant absent.")
    return UUID(str(row["id"]))


def mettre_a_jour_action(action_id: UUID, payload: UpdateActionPayload) -> dict[str, Any]:
    updates: dict[str, Any] = {}

    if payload.annee is not None:
        updates["annee"] = payload.annee
    if payload.categorie is not None:
        _validate_categorie(payload.categorie)
        updates["categorie"] = payload.categorie
    if payload.libelle_prestation is not None:
        updates["libelle_prestation"] = payload.libelle_prestation.strip()
    if payload.statut is not None:
        _validate_statut(payload.statut)
        updates["statut"] = payload.statut
    if payload.cout_ht_prevu is not None:
        updates["cout_ht_prevu"] = payload.cout_ht_prevu
    if payload.prestataire_id is not None:
        updates["prestataire_id"] = str(payload.prestataire_id)
    if payload.unit_id is not None:
        updates["unit_id"] = str(payload.unit_id)
    if payload.note is not None:
        updates["note"] = payload.note

    if not updates:
        raise PlanningCrudError("Aucune donnée à mettre à jour.")

    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("comp_affectation_annuelle")
            .update(updates, returning="representation")
            .eq("id", str(action_id))
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row:
        raise PlanningCrudError("Action introuvable ou mise à jour échouée.")
    return row


def supprimer_action(action_id: UUID) -> None:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("comp_affectation_annuelle")
            .delete(returning="representation")
            .eq("id", str(action_id))
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row:
        raise PlanningCrudError("Action introuvable ou suppression échouée.")


# =============================================================================
# UNITÉS DE GESTION (comp_gestion_unit)
# =============================================================================

TYPES_MILIEU_VALIDES = {"zone_humide", "fosse", "lande", "boisement", "prairie", "autre"}


@dataclass
class CreateUnitePayload:
    projet_id: UUID
    code: str
    type_milieu: str
    libelle: Optional[str] = None
    description: Optional[str] = None


@dataclass
class UpdateUnitePayload:
    code: Optional[str] = None
    type_milieu: Optional[str] = None
    libelle: Optional[str] = None
    description: Optional[str] = None


def lister_unites(projet_id: UUID) -> list[dict[str, Any]]:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("comp_gestion_unit")
            .select("*")
            .eq("projet_id", str(projet_id))
            .order("code")
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    return response.data or []


def creer_unite(payload: CreateUnitePayload) -> UUID:
    if payload.type_milieu not in TYPES_MILIEU_VALIDES:
        raise PlanningCrudError(
            f"Type de milieu invalide '{payload.type_milieu}'. "
            f"Valeurs acceptées : {TYPES_MILIEU_VALIDES}"
        )

    client = _get_supabase_client()
    insert_payload: dict[str, Any] = {
        "projet_id": str(payload.projet_id),
        "code": payload.code.strip().upper(),
        "type_milieu": payload.type_milieu,
        "libelle": payload.libelle,
        "description": payload.description,
    }

    try:
        response = (
            client.schema("bancarisation")
            .table("comp_gestion_unit")
            .insert(insert_payload, returning="representation")
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row or "id" not in row:
        raise PlanningCrudError("Insertion échouée : identifiant absent.")
    return UUID(str(row["id"]))


def mettre_a_jour_unite(unite_id: UUID, payload: UpdateUnitePayload) -> dict[str, Any]:
    updates: dict[str, Any] = {}

    if payload.code is not None:
        updates["code"] = payload.code.strip().upper()
    if payload.type_milieu is not None:
        if payload.type_milieu not in TYPES_MILIEU_VALIDES:
            raise PlanningCrudError(f"Type de milieu invalide '{payload.type_milieu}'.")
        updates["type_milieu"] = payload.type_milieu
    if payload.libelle is not None:
        updates["libelle"] = payload.libelle
    if payload.description is not None:
        updates["description"] = payload.description

    if not updates:
        raise PlanningCrudError("Aucune donnée à mettre à jour.")

    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("comp_gestion_unit")
            .update(updates, returning="representation")
            .eq("id", str(unite_id))
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row:
        raise PlanningCrudError("Unité introuvable ou mise à jour échouée.")
    return row


def supprimer_unite(unite_id: UUID) -> None:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("comp_gestion_unit")
            .delete(returning="representation")
            .eq("id", str(unite_id))
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row:
        raise PlanningCrudError("Unité introuvable ou suppression échouée.")


# =============================================================================
# PRESTATAIRES (comp_prestataire)
# =============================================================================

ROLES_VALIDES = {"mandataire", "sous_traitant", "co_traitant"}


@dataclass
class CreatePrestaPayload:
    nom: str
    role_defaut: str = "sous_traitant"
    siret: Optional[str] = None
    contact_nom: Optional[str] = None
    contact_email: Optional[str] = None


@dataclass
class UpdatePrestaPayload:
    nom: Optional[str] = None
    role_defaut: Optional[str] = None
    siret: Optional[str] = None
    contact_nom: Optional[str] = None
    contact_email: Optional[str] = None
    actif: Optional[bool] = None


def lister_prestataires() -> list[dict[str, Any]]:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("comp_prestataire")
            .select("*")
            .eq("organisation_id", ORGANISATION_ID_V0)
            .eq("actif", True)
            .order("nom")
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    return response.data or []


def creer_prestataire(payload: CreatePrestaPayload) -> UUID:
    if payload.role_defaut not in ROLES_VALIDES:
        raise PlanningCrudError(
            f"Rôle invalide '{payload.role_defaut}'. Valeurs acceptées : {ROLES_VALIDES}"
        )

    client = _get_supabase_client()
    insert_payload: dict[str, Any] = {
        "organisation_id": ORGANISATION_ID_V0,
        "nom": payload.nom.strip(),
        "role_defaut": payload.role_defaut,
        "siret": payload.siret,
        "contact_nom": payload.contact_nom,
        "contact_email": payload.contact_email,
    }

    try:
        response = (
            client.schema("bancarisation")
            .table("comp_prestataire")
            .insert(insert_payload, returning="representation")
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row or "id" not in row:
        raise PlanningCrudError("Insertion échouée : identifiant absent.")
    return UUID(str(row["id"]))


def mettre_a_jour_prestataire(presta_id: UUID, payload: UpdatePrestaPayload) -> dict[str, Any]:
    updates: dict[str, Any] = {}

    if payload.nom is not None:
        updates["nom"] = payload.nom.strip()
    if payload.role_defaut is not None:
        if payload.role_defaut not in ROLES_VALIDES:
            raise PlanningCrudError(f"Rôle invalide '{payload.role_defaut}'.")
        updates["role_defaut"] = payload.role_defaut
    if payload.siret is not None:
        updates["siret"] = payload.siret
    if payload.contact_nom is not None:
        updates["contact_nom"] = payload.contact_nom
    if payload.contact_email is not None:
        updates["contact_email"] = payload.contact_email
    if payload.actif is not None:
        updates["actif"] = payload.actif

    if not updates:
        raise PlanningCrudError("Aucune donnée à mettre à jour.")

    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("comp_prestataire")
            .update(updates, returning="representation")
            .eq("id", str(presta_id))
            .execute()
        )
    except Exception as exc:
        raise PlanningCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row:
        raise PlanningCrudError("Prestataire introuvable ou mise à jour échouée.")
    return row