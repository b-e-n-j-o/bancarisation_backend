"""Lecture budget isolée pour l'export.

Les montants vivent dans occurrence_finance (migration 055). Si une future
table remplace celle-ci, seul ce module est à adapter.
"""

from __future__ import annotations

from typing import Any

import psycopg

from api.export.sqlutil import annee_clause, lignes


def charger_budget_projet(
    conn: psycopg.Connection,
    projet_id: str,
    annee_min: int | None = None,
    annee_max: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    filtre, params_annees = annee_clause("o", annee_min, annee_max)
    detail = lignes(
        conn,
        f"""
        select o.id::text,
               o.annee,
               o.statut,
               o.titre,
               o.code,
               coalesce(af.code, e.code_operation) as mesure_code,
               coalesce(af.titre, e.libelle) as mesure_libelle,
               coalesce(o.ug_ids, '{{}}'::text[]) as ug_ids,
               f.montant_ht,
               f.montant_engage,
               f.montant_realise,
               f.montant_initial,
               f.annee_initiale,
               coalesce(pr.nom, o.prestataire) as prestataire
          from bancarisation.occurrence o
          left join bancarisation.occurrence_finance f on f.occurrence_id = o.id
          left join bancarisation.echeance e on e.id = o.echeance_id
          left join bancarisation.action_fiche af
            on af.projet_id = o.projet_id and af.cle = e.action_cle
          left join bancarisation.prestataires pr on pr.id = o.prestataire_id
         where o.projet_id = %s
           and o.statut not in ('supprime', 'archive')
           {filtre}
         order by o.annee, o.code
        """,
        [projet_id, *params_annees],
    )
    ecarts = lignes(
        conn,
        """
        select annee_ref, annule, glisse_sortant, ajoute, revision_prix
          from bancarisation.v_budget_ecarts
         where projet_id = %s
         order by annee_ref
        """,
        (projet_id,),
    )
    if annee_min is not None:
        ecarts = [e for e in ecarts if e.get("annee_ref") is not None and e["annee_ref"] >= annee_min]
    if annee_max is not None:
        ecarts = [e for e in ecarts if e.get("annee_ref") is not None and e["annee_ref"] <= annee_max]

    baselines = lignes(
        conn,
        """
        select id::text, figee_le, libelle, commentaire, mode, nb_occurrences, total_ht
          from bancarisation.budget_baseline
         where projet_id = %s
         order by figee_le
        """,
        (projet_id,),
    )
    mouvements = lignes(
        conn,
        f"""
        select m.modifie_le, m.occurrence_id::text, o.titre as occurrence,
               m.champ, m.ancienne_val, m.nouvelle_val, m.motif, m.modifie_par
          from bancarisation.budget_mouvement m
          left join bancarisation.occurrence o on o.id = m.occurrence_id
         where m.projet_id = %s
           {filtre}
         order by m.modifie_le
        """,
        [projet_id, *params_annees],
    )
    return {
        "detail": detail,
        "ecarts": ecarts,
        "baselines": baselines,
        "mouvements": mouvements,
    }
