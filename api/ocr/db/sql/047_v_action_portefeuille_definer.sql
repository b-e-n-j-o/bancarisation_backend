-- =============================================================================
-- 047 — v_action_portefeuille lisible par le client anon (page /actions)
-- =============================================================================
-- Ne pas passer par v_occurrence_calendrier (security_invoker=true) : même
-- avec une vue propriétaire, l'invoker imbriqué réapplique la RLS occurrence
-- et le front reçoit 0 ligne. On part de `occurrence` en propriétaire.
-- =============================================================================

BEGIN;

CREATE OR REPLACE VIEW bancarisation.v_action_portefeuille
WITH (security_invoker = false) AS
SELECT
  o.id,
  o.projet_id,
  o.echeance_id,
  o.annee,
  o.code,
  o.titre,
  o.categorie,
  o.statut,
  o.ug_ids,
  o.mois_debut,
  o.mois_fin,
  o.traverse_nouvel_an,
  o.origine,
  o.confiance,
  o.champs_a_confirmer,
  o.avertissements,
  o.modifie_le,
  o.date_realisation,
  o.commentaire,
  e.cle AS echeance_cle,
  e.code_operation,
  e.libelle AS echeance_libelle,
  e.action_cle,
  COALESCE(NULLIF(NULLIF(o.lib_thema, ''::text), 'autre'::text), NULLIF(NULLIF(e.lib_thema, ''::text), 'autre'::text), NULLIF(af.lib_thema, ''::text), 'autre'::text) AS lib_thema,
  o.montant_ht,
  o.montant_ttc,
  o.taux_tva,
  o.prestataire,
  o.ligne_budget_id,
  o.montant_initial,
  o.annee_initiale,
  o.montant_engage,
  o.montant_realise,
  o.prestataire_id,
  COALESCE(prst.nom, o.prestataire) AS prestataire_nom,
  e.recurrence AS echeance_recurrence,
  o.date_realisation_fin,
  o.surface_m2,
  o.responsable_id,
  COALESCE(NULLIF(trim(concat_ws(' ', resp.prenom, resp.nom)), ''), resp.nom) AS responsable_nom,
  p.nom               AS projet_nom,
  p.reference_interne AS projet_reference,
  p.departement       AS projet_departement,
  p.organisation_id,
  org.nom             AS organisation_nom,
  fen.date_fin_fenetre,
  (o.statut NOT IN ('realise', 'supprime')
   AND fen.date_fin_fenetre < current_date) AS en_retard,
  et.nb_etapes,
  et.nb_etapes_faites,
  et.etape_courante,
  nt.nb_notes,
  nt.derniere_note_le
FROM bancarisation.occurrence o
LEFT JOIN bancarisation.echeance e ON e.id = o.echeance_id
LEFT JOIN bancarisation.action_fiche af ON af.projet_id = o.projet_id AND af.cle = e.action_cle
LEFT JOIN bancarisation.prestataires prst ON prst.id = o.prestataire_id
LEFT JOIN bancarisation.personnes resp ON resp.id = o.responsable_id
LEFT JOIN bancarisation.projets p ON p.id = o.projet_id
LEFT JOIN bancarisation.organisations org ON org.id = p.organisation_id
CROSS JOIN LATERAL (
  SELECT (
    make_date(
      o.annee + CASE WHEN coalesce(o.traverse_nouvel_an, false) THEN 1 ELSE 0 END,
      coalesce(o.mois_fin, 12),
      1
    ) + interval '1 month' - interval '1 day'
  )::date AS date_fin_fenetre
) fen
LEFT JOIN LATERAL (
  SELECT
    count(*)::int AS nb_etapes,
    count(*) FILTER (WHERE ae.fait)::int AS nb_etapes_faites,
    (SELECT e2.libelle FROM bancarisation.action_etape e2
      WHERE e2.occurrence_id = o.id AND NOT e2.fait
      ORDER BY e2.ordre LIMIT 1) AS etape_courante
  FROM bancarisation.action_etape ae
  WHERE ae.occurrence_id = o.id
) et ON true
LEFT JOIN LATERAL (
  SELECT
    count(*)::int AS nb_notes,
    max(n.created_at) AS derniere_note_le
  FROM bancarisation.note_interne n
  WHERE n.occurrence_id = o.id AND n.supprime_le IS NULL
) nt ON true;

GRANT SELECT ON bancarisation.v_action_portefeuille TO anon, authenticated;

COMMIT;

NOTIFY pgrst, 'reload schema';
