-- Table documents + liaison occurrence (NULL = document global projet).
-- À exécuter sur la base bancarisation (Supabase).
-- Prérequis : schéma bancarisation, tables projets et occurrence.

-- Bucket Storage Supabase attendu : documents-projet
-- (chemins : {projet_id}/_projet/... ou {projet_id}/occurrences/{occurrence_id}/...)

CREATE TABLE IF NOT EXISTS bancarisation.documents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    occurrence_id   uuid NULL REFERENCES bancarisation.occurrence(id) ON DELETE SET NULL,
    nom             text NOT NULL,
    nom_fichier     text,
    bucket_path     text NOT NULL,
    taille_octets   bigint,
    type_mime       text,
    categorie       text NOT NULL DEFAULT 'technique',
    date_document   date,
    description     text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS documents_projet_idx
  ON bancarisation.documents (projet_id);

CREATE INDEX IF NOT EXISTS documents_projet_occurrence_idx
  ON bancarisation.documents (projet_id, occurrence_id);

CREATE INDEX IF NOT EXISTS documents_categorie_idx
  ON bancarisation.documents (projet_id, categorie);

-- Géométries extraites des uploads cartographie (GeoJSON).
CREATE TABLE IF NOT EXISTS bancarisation.projet_geometries (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id         uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    document_id       uuid REFERENCES bancarisation.documents(id) ON DELETE SET NULL,
    nom               text,
    feature_index     int NOT NULL DEFAULT 0,
    geometry_type     text,
    geometry_geojson  jsonb NOT NULL,
    properties        jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_fichier    text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS projet_geometries_projet_idx
  ON bancarisation.projet_geometries (projet_id);
