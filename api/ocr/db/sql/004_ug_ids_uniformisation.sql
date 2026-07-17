-- Uniformisation ug_ids : listes d'UG sur action_fiche / echeance / occurrence
-- Identité géométrie reste ug_id (text) sur unites_de_gestion_*
--
-- Normalisation : lower + suppression espaces et séparateurs non alphanumériques
--   "UG 1" / "UG1" / "ug1" → "ug1"

CREATE OR REPLACE FUNCTION bancarisation.normalize_ug_id(raw text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT NULLIF(lower(regexp_replace(coalesce(raw, ''), '[^a-zA-Z0-9]', '', 'g')), '');
$$;

CREATE OR REPLACE FUNCTION bancarisation.normalize_ug_ids(arr text[])
RETURNS text[]
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT coalesce(
        array_agg(DISTINCT n ORDER BY n) FILTER (WHERE n IS NOT NULL AND n <> ''),
        '{}'::text[]
    )
    FROM unnest(coalesce(arr, '{}'::text[])) AS u(v)
    CROSS JOIN LATERAL (SELECT bancarisation.normalize_ug_id(v) AS n) s;
$$;

-- 1. Colonne ug_ids sur action_fiche
ALTER TABLE bancarisation.action_fiche
    ADD COLUMN IF NOT EXISTS ug_ids text[] NOT NULL DEFAULT '{}';

-- 2. Backfill depuis fiche_json.unites_gestion (et ug_ids JSON si déjà présent)
UPDATE bancarisation.action_fiche a
SET ug_ids = bancarisation.normalize_ug_ids(
    COALESCE(
        ARRAY(
            SELECT jsonb_array_elements_text(
                CASE
                    WHEN jsonb_typeof(a.fiche_json->'ug_ids') = 'array'
                        THEN a.fiche_json->'ug_ids'
                    WHEN jsonb_typeof(a.fiche_json->'unites_gestion') = 'array'
                        THEN a.fiche_json->'unites_gestion'
                    ELSE '[]'::jsonb
                END
            )
        ),
        '{}'::text[]
    )
)
WHERE a.ug_ids = '{}'::text[]
   OR cardinality(a.ug_ids) = 0;

-- 3. Compléter actions vides via échéances liées (après rename éventuel)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bancarisation'
          AND table_name = 'echeance'
          AND column_name = 'unites_gestion'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bancarisation'
          AND table_name = 'echeance'
          AND column_name = 'ug_ids'
    ) THEN
        ALTER TABLE bancarisation.echeance RENAME COLUMN unites_gestion TO ug_ids;
    ELSIF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bancarisation'
          AND table_name = 'echeance'
          AND column_name = 'unites_gestion'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bancarisation'
          AND table_name = 'echeance'
          AND column_name = 'ug_ids'
    ) THEN
        -- Colonne déjà créée à part : fusion puis drop de l'ancienne
        UPDATE bancarisation.echeance e
        SET ug_ids = bancarisation.normalize_ug_ids(
            COALESCE(e.ug_ids, '{}'::text[]) || COALESCE(e.unites_gestion, '{}'::text[])
        );
        ALTER TABLE bancarisation.echeance DROP COLUMN unites_gestion;
    END IF;
END $$;

-- Garantir ug_ids sur echeance (installs partielles)
ALTER TABLE bancarisation.echeance
    ADD COLUMN IF NOT EXISTS ug_ids text[] NOT NULL DEFAULT '{}';

-- Compléter actions vides depuis échéances (action_cle = cle)
UPDATE bancarisation.action_fiche a
SET ug_ids = sub.ugs
FROM (
    SELECT
        e.projet_id,
        e.action_cle AS cle,
        bancarisation.normalize_ug_ids(array_agg(DISTINCT u)) AS ugs
    FROM bancarisation.echeance e
    CROSS JOIN LATERAL unnest(e.ug_ids) AS u
    WHERE e.action_cle IS NOT NULL AND e.action_cle <> ''
    GROUP BY e.projet_id, e.action_cle
) sub
WHERE a.projet_id = sub.projet_id
  AND a.cle = sub.cle
  AND (a.ug_ids = '{}'::text[] OR cardinality(a.ug_ids) = 0);

-- 5. Normaliser en place
UPDATE bancarisation.action_fiche
SET ug_ids = bancarisation.normalize_ug_ids(ug_ids);

UPDATE bancarisation.echeance
SET ug_ids = bancarisation.normalize_ug_ids(ug_ids);

UPDATE bancarisation.occurrence
SET ug_ids = bancarisation.normalize_ug_ids(ug_ids);

UPDATE bancarisation.unites_de_gestion_surf
SET ug_id = bancarisation.normalize_ug_id(ug_id)
WHERE ug_id IS NOT NULL AND ug_id <> '';

UPDATE bancarisation.unites_de_gestion_lin
SET ug_id = bancarisation.normalize_ug_id(ug_id)
WHERE ug_id IS NOT NULL AND ug_id <> '';

UPDATE bancarisation.unites_de_gestion_pct
SET ug_id = bancarisation.normalize_ug_id(ug_id)
WHERE ug_id IS NOT NULL AND ug_id <> '';

-- 6. Index GIN pour filtres @>
CREATE INDEX IF NOT EXISTS action_fiche_ug_ids_gin
    ON bancarisation.action_fiche USING gin (ug_ids);

CREATE INDEX IF NOT EXISTS echeance_ug_ids_gin
    ON bancarisation.echeance USING gin (ug_ids);

CREATE INDEX IF NOT EXISTS occurrence_ug_ids_gin
    ON bancarisation.occurrence USING gin (ug_ids);
