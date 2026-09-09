-- =============================================================================
-- Migration 039 — FK calques → groupes : ne plus SET NULL sur plan_id
-- =============================================================================
-- PostgreSQL, sur une FK composite ON DELETE SET NULL, met à NULL TOUTES
-- les colonnes (plan_id et groupe_id). plan_id est NOT NULL → 500 au
-- remplacement des groupes. RESTRICT : on détache / supprime les calques
-- avant les groupes (déjà le cas dans persist._ecrire_groupes).
-- =============================================================================

BEGIN;

ALTER TABLE bancarisation.plan_cao_calque
  DROP CONSTRAINT IF EXISTS plan_cao_calque_plan_id_groupe_id_fkey;

ALTER TABLE bancarisation.plan_cao_calque
  DROP CONSTRAINT IF EXISTS plan_cao_calque_groupe_fkey;

ALTER TABLE bancarisation.plan_cao_calque
  ADD CONSTRAINT plan_cao_calque_groupe_fkey
  FOREIGN KEY (plan_id, groupe_id)
  REFERENCES bancarisation.plan_cao_groupe (plan_id, id)
  ON DELETE RESTRICT
  ON UPDATE CASCADE;

COMMIT;
