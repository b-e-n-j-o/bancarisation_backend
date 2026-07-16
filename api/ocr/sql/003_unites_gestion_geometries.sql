-- Unités de gestion + emprise projet (géométries SIG)
-- À exécuter manuellement sur Supabase / PostGIS avant ingestion.
-- geom = CRS source ; geom_3857 = Web Mercator (affichage / analyses web).
-- L'API sert du GeoJSON en EPSG:4326 via ST_Transform(geom_3857, 4326) pour MapLibre.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ── Surfacique (Polygon / MultiPolygon) ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS bancarisation.unites_de_gestion_surf (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    ug_id           text NOT NULL,
    libelle         text NOT NULL DEFAULT '',
    description     text NOT NULL DEFAULT '',
    geom            geometry(MultiPolygon),
    geom_3857       geometry(MultiPolygon, 3857) NOT NULL,
    properties      jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_fichier  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS unites_de_gestion_surf_projet_idx
    ON bancarisation.unites_de_gestion_surf (projet_id);
CREATE INDEX IF NOT EXISTS unites_de_gestion_surf_ug_idx
    ON bancarisation.unites_de_gestion_surf (projet_id, ug_id);
CREATE INDEX IF NOT EXISTS unites_de_gestion_surf_geom_3857_idx
    ON bancarisation.unites_de_gestion_surf USING GIST (geom_3857);

-- ── Linéaire (LineString / MultiLineString) ────────────────────────────────

CREATE TABLE IF NOT EXISTS bancarisation.unites_de_gestion_lin (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    ug_id           text NOT NULL,
    libelle         text NOT NULL DEFAULT '',
    description     text NOT NULL DEFAULT '',
    geom            geometry(MultiLineString),
    geom_3857       geometry(MultiLineString, 3857) NOT NULL,
    properties      jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_fichier  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS unites_de_gestion_lin_projet_idx
    ON bancarisation.unites_de_gestion_lin (projet_id);
CREATE INDEX IF NOT EXISTS unites_de_gestion_lin_ug_idx
    ON bancarisation.unites_de_gestion_lin (projet_id, ug_id);
CREATE INDEX IF NOT EXISTS unites_de_gestion_lin_geom_3857_idx
    ON bancarisation.unites_de_gestion_lin USING GIST (geom_3857);

-- ── Ponctuel (Point / MultiPoint) ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bancarisation.unites_de_gestion_pct (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    ug_id           text NOT NULL,
    libelle         text NOT NULL DEFAULT '',
    description     text NOT NULL DEFAULT '',
    geom            geometry(MultiPoint),
    geom_3857       geometry(MultiPoint, 3857) NOT NULL,
    properties      jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_fichier  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS unites_de_gestion_pct_projet_idx
    ON bancarisation.unites_de_gestion_pct (projet_id);
CREATE INDEX IF NOT EXISTS unites_de_gestion_pct_ug_idx
    ON bancarisation.unites_de_gestion_pct (projet_id, ug_id);
CREATE INDEX IF NOT EXISTS unites_de_gestion_pct_geom_3857_idx
    ON bancarisation.unites_de_gestion_pct USING GIST (geom_3857);

-- ── Emprise du projet (toujours surfacique) ────────────────────────────────

CREATE TABLE IF NOT EXISTS bancarisation.emprise_projet (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    libelle         text NOT NULL DEFAULT 'Emprise projet',
    description     text NOT NULL DEFAULT '',
    geom            geometry(MultiPolygon),
    geom_3857       geometry(MultiPolygon, 3857) NOT NULL,
    properties      jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_fichier  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS emprise_projet_projet_idx
    ON bancarisation.emprise_projet (projet_id);
CREATE INDEX IF NOT EXISTS emprise_projet_geom_3857_idx
    ON bancarisation.emprise_projet USING GIST (geom_3857);
