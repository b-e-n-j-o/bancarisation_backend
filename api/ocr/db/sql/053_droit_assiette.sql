-- 053 — assiette d’un titre : parcelle entière, partie ∩ UG, ou reste hors UG.

ALTER TABLE bancarisation.droits_fonciers
  ADD COLUMN IF NOT EXISTS assiette text NOT NULL DEFAULT 'parcelle_entiere';

ALTER TABLE bancarisation.droits_fonciers
  DROP CONSTRAINT IF EXISTS droits_fonciers_assiette_check;

ALTER TABLE bancarisation.droits_fonciers
  ADD CONSTRAINT droits_fonciers_assiette_check
  CHECK (assiette IN ('parcelle_entiere', 'concerne_ug', 'hors_ug'));

ALTER TABLE bancarisation.droits_fonciers
  ADD COLUMN IF NOT EXISTS ug_code text;

COMMENT ON COLUMN bancarisation.droits_fonciers.assiette IS
  'Périmètre du titre : parcelle entière, intersection avec une UG, ou reste hors UG.';

COMMENT ON COLUMN bancarisation.droits_fonciers.ug_code IS
  'Code UG (ug1, …) quand assiette = concerne_ug.';
