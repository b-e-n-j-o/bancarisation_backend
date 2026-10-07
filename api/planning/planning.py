from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from psycopg.rows import dict_row

from api.db.utilisateur import connect_utilisateur

ORGANISATION_ID_V0 = "a1000000-0000-0000-0000-000000000001"


class PlanningCrudError(Exception):
    pass


def _all(sql: str, params: Any = None) -> list[dict[str, Any]]:
    try:
        with connect_utilisateur(row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
    except Exception as exc:
        raise PlanningCrudError(f"Erreur SQL: {exc}") from exc


def _one(sql: str, params: Any = None) -> dict[str, Any] | None:
    rows = _all(sql, params)
    return rows[0] if rows else None


CATEGORIES_VALIDES = {"MG", "SE", "TU", "TE"}
STATUTS_VALIDES = {"projete", "realise", "supprime", "conditionnel"}


@dataclass
class CreateActionPayload:
    projet_id: UUID
    annee: int
    categorie: str
    libelle_prestation: str
    statut: str = "projete"
    thema_code: Optional[str] = None
    cout_ht_prevu: Optional[float] = None
    prestataire_id: Optional[UUID] = None
    unit_id: Optional[UUID] = None
    note: Optional[str] = None


@dataclass
class UpdateActionPayload:
    annee: Optional[int] = None
    categorie: Optional[str] = None
    libelle_prestation: Optional[str] = None
    statut: Optional[str] = None
    thema_code: Optional[str] = None
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
    return _all(
        """
        SELECT * FROM bancarisation.comp_affectation_annuelle
        WHERE projet_id = %s
        ORDER BY annee, categorie
        """,
        (str(projet_id),),
    )


def lire_action(action_id: UUID) -> dict[str, Any]:
    row = _one(
        "SELECT * FROM bancarisation.comp_affectation_annuelle WHERE id = %s",
        (str(action_id),),
    )
    if not row:
        raise PlanningCrudError("Action introuvable.")
    return row


def creer_action(payload: CreateActionPayload) -> UUID:
    _validate_categorie(payload.categorie)
    _validate_statut(payload.statut)
    row = _one(
        """
        INSERT INTO bancarisation.comp_affectation_annuelle
            (projet_id, annee, categorie, libelle_prestation, statut, cout_ht_prevu,
             prestataire_id, unit_id, note, thema_code)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            str(payload.projet_id),
            payload.annee,
            payload.categorie,
            payload.libelle_prestation.strip(),
            payload.statut,
            payload.cout_ht_prevu,
            str(payload.prestataire_id) if payload.prestataire_id else None,
            str(payload.unit_id) if payload.unit_id else None,
            payload.note,
            (payload.thema_code or "").strip() or None,
        ),
    )
    if not row:
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
    if payload.thema_code is not None:
        updates["thema_code"] = payload.thema_code
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
    sets = ", ".join(f"{k} = %s" for k in updates)
    row = _one(
        f"UPDATE bancarisation.comp_affectation_annuelle SET {sets} WHERE id = %s RETURNING *",
        list(updates.values()) + [str(action_id)],
    )
    if not row:
        raise PlanningCrudError("Action introuvable ou mise à jour échouée.")
    return row


def supprimer_action(action_id: UUID) -> None:
    row = _one(
        "DELETE FROM bancarisation.comp_affectation_annuelle WHERE id = %s RETURNING id",
        (str(action_id),),
    )
    if not row:
        raise PlanningCrudError("Action introuvable ou suppression échouée.")


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
    return _all(
        """
        SELECT * FROM bancarisation.comp_gestion_unit
        WHERE projet_id = %s
        ORDER BY code
        """,
        (str(projet_id),),
    )


def creer_unite(payload: CreateUnitePayload) -> UUID:
    if payload.type_milieu not in TYPES_MILIEU_VALIDES:
        raise PlanningCrudError(
            f"Type de milieu invalide '{payload.type_milieu}'. "
            f"Valeurs acceptées : {TYPES_MILIEU_VALIDES}"
        )
    row = _one(
        """
        INSERT INTO bancarisation.comp_gestion_unit
            (projet_id, code, type_milieu, libelle, description)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            str(payload.projet_id),
            payload.code.strip().upper(),
            payload.type_milieu,
            payload.libelle,
            payload.description,
        ),
    )
    if not row:
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
    sets = ", ".join(f"{k} = %s" for k in updates)
    row = _one(
        f"UPDATE bancarisation.comp_gestion_unit SET {sets} WHERE id = %s RETURNING *",
        list(updates.values()) + [str(unite_id)],
    )
    if not row:
        raise PlanningCrudError("Unité introuvable ou mise à jour échouée.")
    return row


def supprimer_unite(unite_id: UUID) -> None:
    row = _one(
        "DELETE FROM bancarisation.comp_gestion_unit WHERE id = %s RETURNING id",
        (str(unite_id),),
    )
    if not row:
        raise PlanningCrudError("Unité introuvable ou suppression échouée.")


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
    return _all(
        """
        SELECT * FROM bancarisation.comp_prestataire
        WHERE actif = TRUE
        ORDER BY nom
        """
    )


def creer_prestataire(payload: CreatePrestaPayload) -> UUID:
    if payload.role_defaut not in ROLES_VALIDES:
        raise PlanningCrudError(
            f"Rôle invalide '{payload.role_defaut}'. Valeurs acceptées : {ROLES_VALIDES}"
        )
    org = _one(
        "SELECT organisation_id FROM prive.mes_appartenances() WHERE role = 'admin' LIMIT 1"
    )
    org_id = str(org["organisation_id"]) if org else ORGANISATION_ID_V0
    row = _one(
        """
        INSERT INTO bancarisation.comp_prestataire
            (organisation_id, nom, role_defaut, siret, contact_nom, contact_email)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            org_id,
            payload.nom.strip(),
            payload.role_defaut,
            payload.siret,
            payload.contact_nom,
            payload.contact_email,
        ),
    )
    if not row:
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
    sets = ", ".join(f"{k} = %s" for k in updates)
    row = _one(
        f"UPDATE bancarisation.comp_prestataire SET {sets} WHERE id = %s RETURNING *",
        list(updates.values()) + [str(presta_id)],
    )
    if not row:
        raise PlanningCrudError("Prestataire introuvable ou mise à jour échouée.")
    return row
