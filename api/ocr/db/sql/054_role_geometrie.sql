-- Rôle d'une géométrie dans le projet (UG, emprise, plan de masse, zones ERC, complément).
-- À appliquer avec le propriétaire des tables. L'ingestion ne fait plus d'ALTER.

ALTER TABLE bancarisation.unites_de_gestion_surf
    ADD COLUMN IF NOT EXISTS role_geometrie text;

ALTER TABLE bancarisation.unites_de_gestion_lin
    ADD COLUMN IF NOT EXISTS role_geometrie text;

ALTER TABLE bancarisation.unites_de_gestion_pct
    ADD COLUMN IF NOT EXISTS role_geometrie text;

ALTER TABLE bancarisation.emprise_projet
    ADD COLUMN IF NOT EXISTS role_geometrie text;
