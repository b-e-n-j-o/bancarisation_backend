-- Cadastre IGN (PCI) autour des unités de gestion.
-- Snapshot à l'ingestion : une parcelle peut être liée à plusieurs UG (buffers).
-- Exécuter manuellement sur Supabase / PostGIS.

CREATE TABLE IF NOT EXISTS bancarisation.cadastre_parcelle (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    ug_id           text NOT NULL,
    idu             text NOT NULL,
    section         text,
    numero          text,
    code_insee      text,
    nom_com         text,
    contenance      integer,
    geom            geometry(MultiPolygon, 4326),
    geom_3857       geometry(MultiPolygon, 3857) NOT NULL,
    properties      jsonb NOT NULL DEFAULT '{}'::jsonb,
    buffer_m        numeric,
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (projet_id, ug_id, idu)
);

CREATE INDEX IF NOT EXISTS cadastre_parcelle_projet_idx
    ON bancarisation.cadastre_parcelle (projet_id);
CREATE INDEX IF NOT EXISTS cadastre_parcelle_ug_idx
    ON bancarisation.cadastre_parcelle (projet_id, ug_id);
CREATE INDEX IF NOT EXISTS cadastre_parcelle_idu_idx
    ON bancarisation.cadastre_parcelle (projet_id, idu);
CREATE INDEX IF NOT EXISTS cadastre_parcelle_geom_3857_idx
    ON bancarisation.cadastre_parcelle USING GIST (geom_3857);

COMMENT ON TABLE bancarisation.cadastre_parcelle IS
    'Parcelles cadastrales IGN (API Carto) dans un buffer autour de chaque UG.';
