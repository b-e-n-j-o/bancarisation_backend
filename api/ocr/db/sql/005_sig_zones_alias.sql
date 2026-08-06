-- Zones SIG enrichies + table d'alias (rapprochement document ↔ géométrie)
-- Exécuter manuellement sur Supabase / PostGIS.

-- Métadonnées ERC stockées aussi dans properties jsonb (compatibilité immédiate).
-- Colonnes optionnelles pour requêtes directes :

ALTER TABLE bancarisation.unites_de_gestion_surf
    ADD COLUMN IF NOT EXISTS nom_source text,
    ADD COLUMN IF NOT EXISTS categorie_erc text,
    ADD COLUMN IF NOT EXISTS cible text,
    ADD COLUMN IF NOT EXISTS analyse_id text;

ALTER TABLE bancarisation.unites_de_gestion_lin
    ADD COLUMN IF NOT EXISTS nom_source text,
    ADD COLUMN IF NOT EXISTS categorie_erc text,
    ADD COLUMN IF NOT EXISTS cible text,
    ADD COLUMN IF NOT EXISTS analyse_id text;

ALTER TABLE bancarisation.unites_de_gestion_pct
    ADD COLUMN IF NOT EXISTS nom_source text,
    ADD COLUMN IF NOT EXISTS categorie_erc text,
    ADD COLUMN IF NOT EXISTS cible text,
    ADD COLUMN IF NOT EXISTS analyse_id text;

ALTER TABLE bancarisation.emprise_projet
    ADD COLUMN IF NOT EXISTS nom_source text,
    ADD COLUMN IF NOT EXISTS categorie_erc text,
    ADD COLUMN IF NOT EXISTS cible text,
    ADD COLUMN IF NOT EXISTS analyse_id text;

CREATE TABLE IF NOT EXISTS bancarisation.ug_alias (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    zone_id uuid NOT NULL,
    alias text NOT NULL,
    origine text NOT NULL,  -- 'sig' | 'document' | 'manuel'
    confiance numeric,
    valide_par uuid,
    valide_le timestamptz,
    UNIQUE (projet_id, zone_id, alias)
);

CREATE INDEX IF NOT EXISTS ug_alias_projet_idx
    ON bancarisation.ug_alias (projet_id);
