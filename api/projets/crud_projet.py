import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from dotenv import load_dotenv
from supabase import Client, create_client

_BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_DIR / ".env")

ORGANISATION_ID_V0 = "a1000000-0000-0000-0000-000000000001"


@dataclass
class CreateProjetPayload:
    nom: str
    reference_interne: str
    commune: Optional[str] = None
    departement: Optional[str] = None
    date_decision: Optional[date] = None
    duree_annees: Optional[int] = None
    type_procedure: Optional[str] = None


class ProjetCrudError(Exception):
    pass


def _get_supabase_client() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not service_key:
        raise ProjetCrudError(
            "Variables SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY manquantes."
        )

    return create_client(supabase_url, service_key)


def creer_projet(payload: CreateProjetPayload) -> UUID:
    client = _get_supabase_client()

    insert_payload = {
        "nom": payload.nom.strip(),
        "reference_interne": payload.reference_interne.strip(),
        "commune": payload.commune,
        "departement": payload.departement,
        "date_decision": payload.date_decision.isoformat() if payload.date_decision else None,
        "duree_annees": payload.duree_annees,
        "type_procedure": payload.type_procedure,
        "organisation_id": ORGANISATION_ID_V0,
        "statut": "en_instruction",
    }

    try:
        response = (
            client.schema("bancarisation")
            .table("projets")
            .insert(insert_payload, returning="representation")
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ProjetCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data

    if not row or "id" not in row:
        raise ProjetCrudError("Insertion échouée: identifiant de projet absent.")

    return UUID(str(row["id"]))


@dataclass
class UpdateProjetPayload:
    nom: Optional[str] = None
    reference_interne: Optional[str] = None
    commune: Optional[str] = None
    departement: Optional[str] = None
    date_decision: Optional[date] = None
    duree_annees: Optional[int] = None
    type_procedure: Optional[str] = None


def lire_projet(projet_id: UUID) -> dict[str, Any]:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("projets")
            .select("*")
            .eq("id", str(projet_id))
            .maybe_single()
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ProjetCrudError(f"Erreur Supabase: {exc}") from exc

    row = response.data
    if not row:
        raise ProjetCrudError("Projet introuvable.")
    return row


def lister_projets(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("projets")
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ProjetCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data or []
    return data if isinstance(data, list) else [data]


def mettre_a_jour_projet(projet_id: UUID, payload: UpdateProjetPayload) -> dict[str, Any]:
    client = _get_supabase_client()
    updates: dict[str, Any] = {}

    if payload.nom is not None:
        updates["nom"] = payload.nom.strip()
    if payload.reference_interne is not None:
        updates["reference_interne"] = payload.reference_interne.strip()
    if payload.commune is not None:
        updates["commune"] = payload.commune
    if payload.departement is not None:
        updates["departement"] = payload.departement
    if payload.date_decision is not None:
        updates["date_decision"] = payload.date_decision.isoformat()
    if payload.duree_annees is not None:
        updates["duree_annees"] = payload.duree_annees
    if payload.type_procedure is not None:
        updates["type_procedure"] = payload.type_procedure

    if not updates:
        raise ProjetCrudError("Aucune donnée à mettre à jour.")

    try:
        response = (
            client.schema("bancarisation")
            .table("projets")
            .update(updates, returning="representation")
            .eq("id", str(projet_id))
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ProjetCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row:
        raise ProjetCrudError("Projet introuvable ou mise à jour échouée.")
    return row


def supprimer_projet(projet_id: UUID) -> None:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("projets")
            .delete(returning="representation")
            .eq("id", str(projet_id))
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ProjetCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row:
        raise ProjetCrudError("Projet introuvable ou suppression échouée.")


def lister_geometries_projet(projet_id: UUID) -> list[dict[str, Any]]:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("projet_geometries")
            .select("*")
            .eq("projet_id", str(projet_id))
            .order("feature_index")
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ProjetCrudError(f"Erreur Supabase: {exc}") from exc

    data = response.data or []
    return data if isinstance(data, list) else [data]


# Alias de compatibilité avec les imports existants
ProjetCreationError = ProjetCrudError
