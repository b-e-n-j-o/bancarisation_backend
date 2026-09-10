-- =============================================================================
-- Migration 042 — v_action_portefeuille (vue mince)
-- =============================================================================
-- Actions de tous les projets : v_occurrence_calendrier + projet / org / retard.
-- Pas de responsable, étapes ni notes (lots suivants).
-- À lancer à la main :
--   psql "$DATABASE_URL" -f backend/api/ocr/db/sql/042_v_action_portefeuille.sql
-- =============================================================================

BEGIN;

CREATE OR REPLACE VIEW bancarisation.v_action_portefeuille
WITH (security_invoker = true) AS
SELECT
  vc.*,
  p.nom               AS projet_nom,
  p.reference_interne AS projet_reference,
  p.departement       AS projet_departement,
  p.organisation_id,
  org.nom             AS organisation_nom,
  fen.date_fin_fenetre,
  (vc.statut NOT IN ('realise', 'supprime')
   AND fen.date_fin_fenetre < current_date) AS en_retard
FROM bancarisation.v_occurrence_calendrier vc
JOIN bancarisation.projets p              ON p.id = vc.projet_id
LEFT JOIN bancarisation.organisations org ON org.id = p.organisation_id
CROSS JOIN LATERAL (
  SELECT (
    make_date(
      vc.annee + CASE WHEN coalesce(vc.traverse_nouvel_an, false) THEN 1 ELSE 0 END,
      coalesce(vc.mois_fin, 12),
      1
    ) + interval '1 month' - interval '1 day'
  )::date AS date_fin_fenetre
) fen;

GRANT SELECT ON bancarisation.v_action_portefeuille TO anon, authenticated;

COMMIT;

NOTIFY pgrst, 'reload schema';
