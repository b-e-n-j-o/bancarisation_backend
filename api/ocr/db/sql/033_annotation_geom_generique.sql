-- =============================================================================
-- Migration 033 — Annotations terrain : géométrie générique
-- =============================================================================
-- La table 028 ne stockait que des Point. Le poste carto permet désormais
-- des notes rattachées à des lignes, surfaces, et collections (multi-*).
-- Additive : les points existants restent valides.
-- =============================================================================

BEGIN;

ALTER TABLE bancarisation.annotation_terrain
    ALTER COLUMN geom TYPE geometry(Geometry, 4326)
    USING geom;

COMMENT ON COLUMN bancarisation.annotation_terrain.geom IS
    'Emprise de la note (EPSG:4326) : Point, MultiPoint, LineString, MultiLineString, Polygon, MultiPolygon ou GeometryCollection.';

COMMENT ON TABLE bancarisation.annotation_terrain IS
    'Notes / photos terrain géolocalisées (géométrie quelconque EPSG:4326) pour le poste de travail carto.';

COMMIT;
