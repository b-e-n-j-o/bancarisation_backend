"""Journal des actions utilisateur (audit métier transversal).

Usage :
    from api.journal_actions import journaliser

    journaliser(
        action="bilan.generer",
        projet_id=projet_id,
        cible_type="rapport_bilan",
        cible_id=rapport_id,
        detail={"annee": 2021, "version": 3},
        acteur="user@example.com",
    )

Convention `action` : domaine.verbe  (ex. bilan.supprimer, occurrence.modifier).
Table SQL : bancarisation.journal_actions (voir sql/001_journal_actions.sql
ou ocr/db/sql/015_journal_actions.sql).
"""

from .service import journaliser, lister_actions

__all__ = ["journaliser", "lister_actions"]
