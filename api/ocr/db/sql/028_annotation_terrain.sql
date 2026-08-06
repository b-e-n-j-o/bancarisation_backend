-- =============================================================================
-- Migration 028 — Annotations terrain (notes / photos géolocalisées)
-- =============================================================================
-- Points MapLibre pour l'écologue : note + documents (photos, rapports)
-- rattachés à un projet / optionnellement une UG.
-- Additive uniquement.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS bancarisation.annotation_terrain (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    ug_id           text,
    note            text NOT NULL DEFAULT '',
    titre           text,
    geom            geometry(Point, 4326) NOT NULL,
    document_ids    uuid[] NOT NULL DEFAULT '{}',
    auteur          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS annotation_terrain_projet_idx
    ON bancarisation.annotation_terrain (projet_id);

CREATE INDEX IF NOT EXISTS annotation_terrain_ug_idx
    ON bancarisation.annotation_terrain (projet_id, ug_id);

CREATE INDEX IF NOT EXISTS annotation_terrain_geom_idx
    ON bancarisation.annotation_terrain USING GIST (geom);

COMMENT ON TABLE bancarisation.annotation_terrain IS
    'Notes / photos terrain géolocalisées (Point EPSG:4326) pour le poste de travail carto.';

COMMIT;
