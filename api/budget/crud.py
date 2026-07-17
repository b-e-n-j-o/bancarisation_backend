"""Lecture des lignes budgétaires ingérées."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_database_url

from .ingestion import BudgetIngestError


def lister_lignes_budget(projet_id: UUID) -> dict[str, Any]:
    try:
        with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id::text,
                        projet_id::text,
                        import_id::text,
                        libelle_prestation,
                        libelle_action,
                        code_mesure,
                        prestataire,
                        montant_ht::float8 AS montant_ht,
                        montant_ttc::float8 AS montant_ttc,
                        taux_tva::float8 AS taux_tva,
                        unite,
                        quantite::float8 AS quantite,
                        nb_campagnes,
                        annees,
                        ug_ids,
                        est_total,
                        statut_reel,
                        action_associee,
                        confiance,
                        champs_a_confirmer,
                        source_feuille,
                        source_lignes,
                        created_at::text
                    FROM bancarisation.ligne_budget
                    WHERE projet_id = %s
                    ORDER BY est_total ASC, libelle_prestation ASC, created_at ASC
                    """,
                    (str(projet_id),),
                )
                lignes = [dict(row) for row in cur.fetchall()]

                cur.execute(
                    """
                    SELECT
                        id::text,
                        fichier_nom,
                        devise,
                        nb_lignes,
                        nb_avertissements,
                        avertissements,
                        created_at::text
                    FROM bancarisation.budget_import
                    WHERE projet_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (str(projet_id),),
                )
                dernier_import = cur.fetchone()
    except Exception as exc:
        raise BudgetIngestError(f"Lecture budget impossible : {exc}") from exc

    for ligne in lignes:
        if isinstance(ligne.get("annees"), str):
            try:
                ligne["annees"] = json.loads(ligne["annees"])
            except json.JSONDecodeError:
                ligne["annees"] = {}

    meta = None
    if dernier_import:
        meta = dict(dernier_import)
        if isinstance(meta.get("avertissements"), str):
            try:
                meta["avertissements"] = json.loads(meta["avertissements"])
            except json.JSONDecodeError:
                meta["avertissements"] = []

    detail = [l for l in lignes if not l.get("est_total")]
    totaux = [l for l in lignes if l.get("est_total")]

    return {
        "lignes": lignes,
        "meta": meta,
        "stats": {
            "nb_lignes": len(lignes),
            "nb_detail": len(detail),
            "nb_totaux": len(totaux),
        },
    }
