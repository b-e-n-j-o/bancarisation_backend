-- =============================================================================
-- Migration 038 — geom_local accepte le Z (points cotés, courbes, INSERT 3D)
-- =============================================================================
-- 035 a créé geometry(Geometry, 0) = XY strict. L'éclatement DXF conserve Z
-- (aplatir_z=False). PostGIS refuse alors l'INSERT :
--   Geometry has Z dimension but column does not
-- Additive. geom / geom_3857 restent 2D (calage via ST_Force2D).
-- =============================================================================

BEGIN;

ALTER TABLE bancarisation.plan_cao_entite
  ALTER COLUMN geom_local TYPE geometry
  USING geom_local;

COMMENT ON COLUMN bancarisation.plan_cao_entite.geom_local IS
  'Géométrie brute du dessin (SRID 0), XY ou XYZ. Jamais écrasée par le calage.';

COMMIT;
