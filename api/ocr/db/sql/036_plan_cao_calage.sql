-- =============================================================================
-- Migration 036 — Index spatiaux après calage DXF (geom 2154 / geom_3857)
-- Les colonnes existent déjà (035). Additive.
-- =============================================================================

BEGIN;

CREATE INDEX IF NOT EXISTS idx_pce_geom
  ON bancarisation.plan_cao_entite USING gist (geom)
  WHERE geom IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pce_geom_3857
  ON bancarisation.plan_cao_entite USING gist (geom_3857)
  WHERE geom_3857 IS NOT NULL;

COMMENT ON COLUMN bancarisation.plan_cao.calage_mode IS
  'non_cale | srid_direct | deux_points | manuel. geom_local jamais écrasé.';

COMMIT;
