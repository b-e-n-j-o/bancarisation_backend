"""Ingestion JSON extraction budget → tables bancarisation."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from api.db.env import get_database_url
from api.ocr.domain.ug_ids import normalize_ug_ids

class BudgetIngestError(Exception):
    pass


def _parse_payload(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if "extraction" in raw:
        carto = raw.get("cartographie") or {}
        extraction = raw["extraction"]
        return carto, extraction
    if "lignes" in raw:
        return {}, raw
    raise BudgetIngestError(
        "JSON invalide : attendu { cartographie, extraction } ou { lignes, totaux_declares }."
    )


def ingérer_budget_json(
    *,
    projet_id: UUID,
    payload: dict[str, Any],
    fichier_nom: str = "lignes_budget.json",
    fichier_hash: str | None = None,
    modele_llm: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    carto, extraction = _parse_payload(payload)
    lignes = extraction.get("lignes") or []
    if not isinstance(lignes, list):
        raise BudgetIngestError("Champ extraction.lignes invalide.")

    totaux = extraction.get("totaux_declares") or []
    avertissements = extraction.get("avertissements") or []
    devise = carto.get("devise") or "EUR"

    if not fichier_hash:
        fichier_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    try:
        with psycopg.connect(get_database_url()) as conn:
            with conn.cursor() as cur:
                if replace:
                    cur.execute(
                        """
                        DELETE FROM bancarisation.ligne_budget
                        WHERE projet_id = %s
                        """,
                        (str(projet_id),),
                    )
                    cur.execute(
                        """
                        DELETE FROM bancarisation.budget_import
                        WHERE projet_id = %s
                        """,
                        (str(projet_id),),
                    )

                cur.execute(
                    """
                    INSERT INTO bancarisation.budget_import (
                        projet_id, fichier_nom, fichier_hash, modele_llm, devise,
                        nb_lignes, nb_totaux, nb_avertissements,
                        cartographie_json, avertissements
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    RETURNING id::text
                    """,
                    (
                        str(projet_id),
                        fichier_nom,
                        fichier_hash,
                        modele_llm,
                        devise,
                        len(lignes),
                        len(totaux) if isinstance(totaux, list) else 0,
                        len(avertissements) if isinstance(avertissements, list) else 0,
                        Jsonb(carto if isinstance(carto, dict) else {}),
                        Jsonb(avertissements if isinstance(avertissements, list) else []),
                    ),
                )
                import_id = cur.fetchone()[0]

                inserted = 0
                for ligne in lignes:
                    if not isinstance(ligne, dict):
                        continue
                    source = ligne.get("source") or {}
                    ug_ids = normalize_ug_ids(ligne.get("ug_mentionnees") or [])
                    annees = ligne.get("annees") or {}
                    if not isinstance(annees, dict):
                        annees = {}
                    source_lignes = source.get("lignes") or []
                    if not isinstance(source_lignes, list):
                        source_lignes = []

                    cur.execute(
                        """
                        INSERT INTO bancarisation.ligne_budget (
                            projet_id, import_id,
                            libelle_prestation, libelle_action, code_mesure, prestataire,
                            montant_ht, montant_ttc, taux_tva, unite, quantite, nb_campagnes,
                            annees, ug_ids, est_total, statut_reel, action_associee,
                            confiance, champs_a_confirmer,
                            source_feuille, source_lignes, ligne_json
                        )
                        VALUES (
                            %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            %s::jsonb, %s, %s, %s, %s,
                            %s, %s,
                            %s, %s, %s::jsonb
                        )
                        """,
                        (
                            str(projet_id),
                            import_id,
                            str(ligne.get("libelle_prestation") or "Sans libellé"),
                            ligne.get("libelle_action"),
                            ligne.get("code_mesure"),
                            ligne.get("prestataire"),
                            ligne.get("montant_ht"),
                            ligne.get("montant_ttc"),
                            ligne.get("taux_tva"),
                            ligne.get("unite"),
                            ligne.get("quantite"),
                            ligne.get("nb_campagnes"),
                            Jsonb(annees),
                            ug_ids,
                            bool(ligne.get("est_total")),
                            ligne.get("statut_reel"),
                            ligne.get("action_associee"),
                            ligne.get("confiance"),
                            ligne.get("champs_a_confirmer") or [],
                            source.get("feuille"),
                            [int(x) for x in source_lignes if isinstance(x, int) or (isinstance(x, str) and x.isdigit())],
                            Jsonb(ligne),
                        ),
                    )
                    inserted += 1

            conn.commit()
    except BudgetIngestError:
        raise
    except Exception as exc:
        raise BudgetIngestError(f"Erreur base de données : {exc}") from exc

    return {
        "import_id": import_id,
        "nb_lignes": inserted,
        "devise": devise,
        "nb_avertissements": len(avertissements) if isinstance(avertissements, list) else 0,
        "replace": replace,
    }
