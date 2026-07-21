"""Historique budgétaire : contexte de session + lecture.

Le trigger bancarisation.log_budget_mouvement (migration 011 / SQL déjà run)
enregistre automatiquement tout changement de montant/statut/année sur
occurrence. Il lit le motif et l'auteur dans deux réglages de session que
l'appli doit poser AVANT l'UPDATE, DANS LA MÊME TRANSACTION.

Important — PATCH occurrence actuel (api/ocr) passe par Supabase REST :
le trigger tourne quand même (historique sans motif). Pour poser un motif,
il faut un UPDATE psycopg (voir modifier_occurrence_avec_contexte) dans la
même transaction que appliquer_contexte_mouvement.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_database_url
from api.ocr.domain.ug_ids import normalize_ug_ids

# Colonnes occurrence que le trigger surveille + autres champs PATCH utiles
# via le chemin psycopg (quand un motif est fourni).
_CHAMPS_PG = frozenset(
    {
        "annee",
        "code",
        "titre",
        "categorie",
        "lib_thema",
        "statut",
        "ug_ids",
        "mois_debut",
        "mois_fin",
        "traverse_nouvel_an",
        "date_realisation",
        "commentaire",
        "montant_ht",
        "montant_ttc",
        "taux_tva",
        "prestataire",
        "prestataire_id",
        "ligne_budget_id",
        "montant_engage",
        "montant_realise",
    }
)


def appliquer_contexte_mouvement(
    cur: psycopg.Cursor,
    *,
    motif: str | None,
    modifie_par: str | None,
) -> None:
    """Pose motif + auteur pour le trigger, sur la transaction courante.

    set_config(..., is_local => true) = équivalent de SET LOCAL, mais
    paramétrable proprement (pas d'injection). Ne fait rien d'utile si la
    connexion est en autocommit — le PATCH doit être en transaction.
    """
    cur.execute(
        "SELECT set_config('bancarisation.motif', %s, true), "
        "       set_config('bancarisation.modifie_par', %s, true)",
        (motif or "", modifie_par or ""),
    )


def modifier_occurrence_avec_contexte(
    occurrence_id: UUID,
    champs: dict[str, Any],
    *,
    motif: str | None = None,
    modifie_par: str | None = None,
) -> dict[str, Any] | None:
    """UPDATE occurrence via psycopg + contexte session pour le trigger.

    À utiliser quand un motif doit être tracé. Sinon le PATCH Supabase
    (crud.modifier_occurrence) suffit : le trigger logue quand même, motif NULL.
    """
    maj = {k: v for k, v in champs.items() if k in _CHAMPS_PG}
    if not maj:
        raise ValueError(f"Aucun champ modifiable. Autorisés : {sorted(_CHAMPS_PG)}")
    if "ug_ids" in maj:
        maj["ug_ids"] = normalize_ug_ids(maj["ug_ids"])

    maj["modifie_le"] = datetime.now(timezone.utc)
    sets = ", ".join(f"{col} = %s" for col in maj)
    values = list(maj.values()) + [str(occurrence_id)]

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            appliquer_contexte_mouvement(cur, motif=motif, modifie_par=modifie_par)
            cur.execute(
                f"""
                UPDATE bancarisation.occurrence
                SET {sets}
                WHERE id = %s
                RETURNING *
                """,
                values,
            )
            row = cur.fetchone()
    return dict(row) if row else None


def _mouvements(where: str, param: str, limite: int) -> list[dict[str, Any]]:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    m.id::text,
                    m.occurrence_id::text,
                    m.champ,
                    m.ancienne_val,
                    m.nouvelle_val,
                    m.motif,
                    m.modifie_par,
                    m.modifie_le::text,
                    o.titre    AS occurrence_titre,
                    o.code     AS occurrence_code,
                    o.annee    AS occurrence_annee
                FROM bancarisation.budget_mouvement m
                JOIN bancarisation.occurrence o ON o.id = m.occurrence_id
                WHERE {where}
                ORDER BY m.modifie_le DESC
                LIMIT %s
                """,
                (param, limite),
            )
            return [dict(r) for r in cur.fetchall()]


def lister_mouvements_occurrence(occurrence_id: UUID, limite: int = 100) -> list[dict[str, Any]]:
    return _mouvements("m.occurrence_id = %s", str(occurrence_id), limite)


def lister_mouvements_projet(projet_id: UUID, limite: int = 500) -> list[dict[str, Any]]:
    return _mouvements("m.projet_id = %s", str(projet_id), limite)
