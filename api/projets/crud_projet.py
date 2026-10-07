from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional
from uuid import UUID

from psycopg.rows import dict_row

from api.db.utilisateur import connect_utilisateur

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


def _one(sql: str, params: Any = None) -> dict[str, Any] | None:
    with connect_utilisateur(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def _all(sql: str, params: Any = None) -> list[dict[str, Any]]:
    with connect_utilisateur(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def creer_projet(payload: CreateProjetPayload) -> UUID:
    org = _one(
        """
        SELECT organisation_id
        FROM prive.mes_appartenances()
        WHERE role = 'admin'
        LIMIT 1
        """
    )
    if not org:
        raise ProjetCrudError("Aucune organisation administrée pour créer un projet.")
    insert_payload = {
        "nom": payload.nom.strip(),
        "organisation_id": str(org["organisation_id"]),
        "statut": "en_instruction",
        "type_dispositif": payload.type_dispositif or "obligation",
        "reference_interne": (payload.reference_interne or "").strip() or None,
        "commune": payload.commune,
        "departement": payload.departement,
        "date_decision": payload.date_decision,
        "duree_annees": payload.duree_annees,
        "type_procedure": payload.type_procedure,
        "cree_par": None,
    }
    row = _one(
        """
        INSERT INTO bancarisation.projets
            (nom, organisation_id, statut, type_dispositif, reference_interne,
             commune, departement, date_decision, duree_annees, type_procedure, cree_par)
        VALUES (%(nom)s, %(organisation_id)s, %(statut)s, %(type_dispositif)s,
                %(reference_interne)s, %(commune)s, %(departement)s, %(date_decision)s,
                %(duree_annees)s, %(type_procedure)s, auth.uid())
        RETURNING id
        """,
        insert_payload,  # type: ignore[arg-type]
    )
    if not row:
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
    row = _one("SELECT * FROM bancarisation.projets WHERE id = %s", (str(projet_id),))
    if not row:
        raise ProjetCrudError("Projet introuvable.")
    return row


def lister_projets(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    try:
        return _all(
            """
            SELECT * FROM bancarisation.v_projet_liste_resume
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
    except Exception:
        return _all(
            """
            SELECT * FROM bancarisation.projets
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )


def mettre_a_jour_projet(projet_id: UUID, payload: UpdateProjetPayload) -> dict[str, Any]:
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
        updates["date_decision"] = payload.date_decision
    if payload.duree_annees is not None:
        updates["duree_annees"] = payload.duree_annees
    if payload.type_procedure is not None:
        updates["type_procedure"] = payload.type_procedure
    if payload.type_dispositif is not None:
        updates["type_dispositif"] = payload.type_dispositif
    if not updates:
        raise ProjetCrudError("Aucune donnée à mettre à jour.")
    sets = ", ".join(f"{k} = %s" for k in updates)
    params = list(updates.values()) + [str(projet_id)]
    row = _one(
        f"UPDATE bancarisation.projets SET {sets} WHERE id = %s RETURNING *",
        tuple(params),
    )
    if not row:
        raise ProjetCrudError("Projet introuvable ou mise à jour échouée.")
    return row


def supprimer_projet(projet_id: UUID) -> None:
    row = _one(
        "DELETE FROM bancarisation.projets WHERE id = %s RETURNING id",
        (str(projet_id),),
    )
    if not row:
        raise ProjetCrudError("Projet introuvable ou suppression échouée.")


def lister_geometries_projet(projet_id: UUID) -> list[dict[str, Any]]:
    return _all(
        """
        SELECT * FROM bancarisation.projet_geometries
        WHERE projet_id = %s
        ORDER BY feature_index, created_at
        """,
        (str(projet_id),),
    )


def lire_organisation(organisation_id: UUID) -> dict[str, Any] | None:
    row = _one(
        "SELECT id::text, nom FROM bancarisation.organisations WHERE id = %s",
        (str(organisation_id),),
    )
    if not row:
        return None
    return {"id": row["id"], "nom": row["nom"]}


def lire_session() -> dict[str, Any]:
    profil = _one(
        """
        SELECT utilisateur_id, nom, prenom, admin_plateforme
        FROM bancarisation.profils
        WHERE utilisateur_id = auth.uid()
        """
    )
    apps = _all(
        """
        SELECT a.organisation_id, a.role, a.acces_global, o.nom AS organisation_nom
        FROM prive.mes_appartenances() a
        JOIN bancarisation.organisations o ON o.id = a.organisation_id
        """
    )
    primaire = next((a for a in apps if a.get("role") == "admin"), apps[0] if apps else None)
    return {
        "user_id": profil["utilisateur_id"] if profil else None,
        "role": primaire["role"] if primaire else "membre",
        "organisation_id": primaire["organisation_id"] if primaire else None,
        "organisation_nom": primaire["organisation_nom"] if primaire else None,
        "admin_plateforme": bool(profil["admin_plateforme"]) if profil else False,
        "appartenances": apps,
    }


def compter_catalogue() -> dict[str, int]:
    utilisateur = _one("SELECT count(*)::int AS n FROM bancarisation.projets")
    geomce = 0
    try:
        row = _one(
            """
            SELECT count(DISTINCT dossier_no)::int AS n
            FROM bancarisation.v_frontend_mesures_projets
            WHERE dossier_no IS NOT NULL AND btrim(dossier_no) <> ''
            """
        )
        geomce = int((row or {}).get("n") or 0)
    except Exception:
        geomce = 0
    return {"utilisateur": int((utilisateur or {}).get("n") or 0), "geomce": geomce}


ProjetCreationError = ProjetCrudError
