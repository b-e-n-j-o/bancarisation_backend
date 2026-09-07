import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import psycopg
from dotenv import load_dotenv
from supabase import Client, create_client

from api.db.env import get_database_url

_BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(_BACKEND_DIR / ".env")

ORGANISATION_ID_V0 = "a1000000-0000-0000-0000-000000000001"


@dataclass
class CreateProjetPayload:
    nom: str
    reference_interne: Optional[str] = None
    commune: Optional[str] = None
    departement: Optional[str] = None
    date_decision: Optional[date] = None
    duree_annees: Optional[int] = None
    type_procedure: Optional[str] = None
    type_dispositif: str = "obligation"


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

    insert_payload: dict[str, Any] = {
        "nom": payload.nom.strip(),
        "organisation_id": ORGANISATION_ID_V0,
        "statut": "en_instruction",
        "type_dispositif": payload.type_dispositif or "obligation",
    }
    if payload.reference_interne and payload.reference_interne.strip():
        insert_payload["reference_interne"] = payload.reference_interne.strip()
    if payload.commune:
        insert_payload["commune"] = payload.commune
    if payload.departement:
        insert_payload["departement"] = payload.departement
    if payload.date_decision:
        insert_payload["date_decision"] = payload.date_decision.isoformat()
    if payload.duree_annees is not None:
        insert_payload["duree_annees"] = payload.duree_annees
    if payload.type_procedure:
        insert_payload["type_procedure"] = payload.type_procedure

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
    type_dispositif: Optional[str] = None
    partager_budget_dreal: Optional[bool] = None


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

    # maybe_single() peut renvoyer None (HTTP 406/404 PostgREST) si aucune ligne
    if response is None or not response.data:
        raise ProjetCrudError("Projet introuvable.")
    return response.data


def lister_projets(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """Liste projets avec résumé calendrier (vue v_projet_liste_resume).

    Fallback sur la table `projets` si la vue n'est pas encore déployée.
    """
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("v_projet_liste_resume")
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        data = response.data or []
        return data if isinstance(data, list) else [data]
    except Exception:
        # Vue absente → liste minimale (sans prochaine mesure)
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
    if payload.type_dispositif is not None:
        updates["type_dispositif"] = payload.type_dispositif
    if payload.partager_budget_dreal is not None:
        updates["partager_budget_dreal"] = bool(payload.partager_budget_dreal)

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


def lire_organisation(organisation_id: UUID) -> dict[str, Any] | None:
    """Lit une organisation par id (nom affiché en session)."""
    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text, nom
                    FROM bancarisation.organisations
                    WHERE id = %s
                    """,
                    (str(organisation_id),),
                )
                row = cur.fetchone()
    except Exception as exc:  # pragma: no cover
        raise ProjetCrudError(f"Erreur lecture organisation: {exc}") from exc

    if not row:
        return None
    return {"id": row[0], "nom": row[1]}


def compter_catalogue() -> dict[str, int]:
    """Décompte total des entités : projets utilisateur + dossiers GEOMCE."""
    utilisateur = 0
    geomce = 0
    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*)::int FROM bancarisation.projets")
                utilisateur = int((cur.fetchone() or [0])[0] or 0)
                try:
                    cur.execute(
                        """
                        SELECT count(DISTINCT dossier_no)::int
                        FROM bancarisation.v_frontend_mesures_projets
                        WHERE dossier_no IS NOT NULL
                          AND btrim(dossier_no) <> ''
                        """
                    )
                    geomce = int((cur.fetchone() or [0])[0] or 0)
                except Exception:
                    conn.rollback()
                    geomce = 0
    except Exception as exc:  # pragma: no cover
        raise ProjetCrudError(f"Erreur décompte catalogue: {exc}") from exc

    return {"utilisateur": utilisateur, "geomce": geomce}


# Alias de compatibilité avec les imports existants
ProjetCreationError = ProjetCrudError
