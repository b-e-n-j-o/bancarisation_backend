-- =============================================================================
-- Migration 037 — Contexte DXF : bloc, attributs, couleur, état des calques
-- =============================================================================
-- Ce que l'éclatement perdait : nom de bloc, ATTRIB rattachés à la géométrie,
-- couleur résolue, calques AutoCAD éteints/gelés, altimétrie par calque.
-- Additive. geom_local inchangé (peut porter un Z).
-- =============================================================================

BEGIN;

ALTER TABLE bancarisation.plan_cao_entite
  ADD COLUMN IF NOT EXISTS bloc text,
  ADD COLUMN IF NOT EXISTS attributs jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS couleur text;

CREATE INDEX IF NOT EXISTS idx_pce_attributs
  ON bancarisation.plan_cao_entite USING gin (attributs);

CREATE INDEX IF NOT EXISTS idx_pce_bloc
  ON bancarisation.plan_cao_entite (plan_id, bloc)
  WHERE bloc IS NOT NULL;

ALTER TABLE bancarisation.plan_cao_calque
  ADD COLUMN IF NOT EXISTS couleur text,
  ADD COLUMN IF NOT EXISTS aci int,
  ADD COLUMN IF NOT EXISTS eteint boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS gele boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS verrouille boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS porte_altimetrie boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS z_min double precision,
  ADD COLUMN IF NOT EXISTS z_max double precision;

ALTER TABLE bancarisation.plan_cao
  ALTER COLUMN calque_0_inclus SET DEFAULT true;

COMMENT ON COLUMN bancarisation.plan_cao.calque_0_inclus IS
  'True : le calque 0 est extrait (points cotés, blocs). False uniquement si exclus à l''import.';

COMMENT ON COLUMN bancarisation.plan_cao_entite.bloc IS
  'Nom du bloc INSERT dont l''entité est issue (ex. ARBRE).';
COMMENT ON COLUMN bancarisation.plan_cao_entite.attributs IS
  'ATTRIB du bloc parent (NUMERO, ESSENCE, DIAM_TRONC, fil d''eau…).';
COMMENT ON COLUMN bancarisation.plan_cao_entite.couleur IS
  'Couleur résolue (#rrggbb) : true_color > ACI > ByBlock > ByLayer.';
COMMENT ON COLUMN bancarisation.plan_cao_calque.eteint IS
  'Calque éteint dans AutoCAD : masqué par défaut, données conservées.';
COMMENT ON COLUMN bancarisation.plan_cao_calque.porte_altimetrie IS
  'True si les Z du calque varient (courbes de niveau, points cotés).';

COMMIT;
