-- Table attributaire source (DBF / GeoPackage / etc.) conservée à l'ingestion.
-- Une UG = N features source → attributs = tableau JSON [{col: val}, …].
-- Exécuter manuellement sur Supabase / PostGIS.

ALTER TABLE bancarisation.unites_de_gestion_surf
    ADD COLUMN IF NOT EXISTS attributs jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE bancarisation.unites_de_gestion_lin
    ADD COLUMN IF NOT EXISTS attributs jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE bancarisation.unites_de_gestion_pct
    ADD COLUMN IF NOT EXISTS attributs jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE bancarisation.emprise_projet
    ADD COLUMN IF NOT EXISTS attributs jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN bancarisation.unites_de_gestion_surf.attributs IS
    'Table attributaire source (lignes = features du .shp / couche), sans géométrie.';
COMMENT ON COLUMN bancarisation.unites_de_gestion_lin.attributs IS
    'Table attributaire source (lignes = features du .shp / couche), sans géométrie.';
COMMENT ON COLUMN bancarisation.unites_de_gestion_pct.attributs IS
    'Table attributaire source (lignes = features du .shp / couche), sans géométrie.';
COMMENT ON COLUMN bancarisation.emprise_projet.attributs IS
    'Table attributaire source (lignes = features du .shp / couche), sans géométrie.';
