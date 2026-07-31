-- Captures Sentinel-2 mises en cache (PNG preview + GeoTIFF bands).
-- À exécuter sur la base bancarisation (Supabase).
-- Bucket Storage attendu : documents-projet
-- Chemins : {projet_id}/satellite/{ug_id}/{date}/truecolor.png | bands.tif

CREATE TABLE IF NOT EXISTS bancarisation.satellite_captures (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id           uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    ug_id               text NOT NULL,
    acquisition_date    date NOT NULL,
    scene_id            text,
    cloud_cover         double precision,
    bbox_4326           jsonb NOT NULL,
    epsg                int NOT NULL DEFAULT 3035,
    resolution_m        int NOT NULL DEFAULT 10,
    width_px            int,
    height_px           int,
    bucket_path_png     text,
    bucket_path_tif     text,
    document_id_png     uuid REFERENCES bancarisation.documents(id) ON DELETE SET NULL,
    document_id_tif     uuid REFERENCES bancarisation.documents(id) ON DELETE SET NULL,
    status              text NOT NULL DEFAULT 'ready'
                        CHECK (status IN ('ready', 'failed', 'pending')),
    error_message       text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT satellite_captures_projet_ug_date_uidx
      UNIQUE (projet_id, ug_id, acquisition_date)
);

CREATE INDEX IF NOT EXISTS satellite_captures_projet_idx
  ON bancarisation.satellite_captures (projet_id);

CREATE INDEX IF NOT EXISTS satellite_captures_projet_ug_idx
  ON bancarisation.satellite_captures (projet_id, ug_id);

CREATE INDEX IF NOT EXISTS satellite_captures_date_idx
  ON bancarisation.satellite_captures (acquisition_date);
