-- =============================================================================
-- Migration 034 — Notes terrain : lignage géométrie + date d'observation
-- =============================================================================
-- Plusieurs notes peuvent porter sur la même emprise (historique).
-- source_geom_key identifie la lignée (tracé, import, UG, autre note).
-- Additive.
-- =============================================================================

BEGIN;

ALTER TABLE bancarisation.annotation_terrain
    ADD COLUMN IF NOT EXISTS source_kind text NOT NULL DEFAULT 'draw',
    ADD COLUMN IF NOT EXISTS source_geom_key text,
    ADD COLUMN IF NOT EXISTS source_annotation_id uuid
        REFERENCES bancarisation.annotation_terrain(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS source_ug_geom_id text,
    ADD COLUMN IF NOT EXISTS observed_at date NOT NULL DEFAULT CURRENT_DATE;

UPDATE bancarisation.annotation_terrain
SET source_geom_key = id::text
WHERE source_geom_key IS NULL;

ALTER TABLE bancarisation.annotation_terrain
    ALTER COLUMN source_geom_key SET NOT NULL;

ALTER TABLE bancarisation.annotation_terrain
    DROP CONSTRAINT IF EXISTS annotation_terrain_source_kind_check;

ALTER TABLE bancarisation.annotation_terrain
    ADD CONSTRAINT annotation_terrain_source_kind_check
    CHECK (source_kind IN ('draw', 'import', 'ug', 'annotation'));

CREATE INDEX IF NOT EXISTS annotation_terrain_source_key_idx
    ON bancarisation.annotation_terrain (projet_id, source_geom_key);

CREATE INDEX IF NOT EXISTS annotation_terrain_observed_idx
    ON bancarisation.annotation_terrain (projet_id, observed_at DESC);

COMMENT ON COLUMN bancarisation.annotation_terrain.source_geom_key IS
    'Clé de lignée : notes partageant la même emprise (historique).';
COMMENT ON COLUMN bancarisation.annotation_terrain.observed_at IS
    'Date métier de la note (visite / opération), distincte de created_at.';

COMMIT;
