-- =============================================================================
-- Migration 040 — CRS par calque (DXF multi-CRS)
-- =============================================================================
-- Constaté sur 19.054 APS : plan de masse en CC45, couches BE/cadastre en
-- Lambert-93. Le srid_source ne peut plus être unique au niveau du plan.
--
-- À lancer à la main sur la base bancarisation :
--   psql "$DATABASE_URL" -f backend/api/ocr/db/sql/040_plan_cao_srid_calque.sql
-- Additive. geom_local inchangé.
-- =============================================================================

BEGIN;

-- 1. le CRS déclaré par le déposant (formulaire, pas une vérité imposée)
ALTER TABLE bancarisation.plan_cao
  ADD COLUMN IF NOT EXISTS srid_declare int,
  ADD COLUMN IF NOT EXISTS srid_declare_origine text
      CHECK (srid_declare_origine IN ('formulaire', 'geodata', 'detection')),
  ADD COLUMN IF NOT EXISTS multi_crs boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS analyse_crs jsonb;

COMMENT ON COLUMN bancarisation.plan_cao.srid_declare IS
  'CRS déclaré au dépôt (formulaire / GEODATA). NULL = local, inconnu, ou « je ne sais pas ». Proposition, jamais vérité imposée.';
COMMENT ON COLUMN bancarisation.plan_cao.srid_declare_origine IS
  'D''où vient srid_declare : formulaire, geodata Civil 3D, ou detection.';
COMMENT ON COLUMN bancarisation.plan_cao.multi_crs IS
  'True si le recoupement géographique confirme plusieurs CRS dans le même DXF.';
COMMENT ON COLUMN bancarisation.plan_cao.analyse_crs IS
  'Rapport JSON de dxf_crs_clusters (clusters, recoupement, srid par calque, comparaison).';

-- 2. le CRS effectif, par calque ; NULL = héritage plan_cao.srid_declare
ALTER TABLE bancarisation.plan_cao_calque
  ADD COLUMN IF NOT EXISTS srid_source int,
  ADD COLUMN IF NOT EXISTS srid_origine text NOT NULL DEFAULT 'herite'
      CHECK (srid_origine IN ('herite', 'detection', 'user')),
  ADD COLUMN IF NOT EXISTS srid_confiance text
      CHECK (srid_confiance IN ('haute', 'a_verifier', 'inconnue')),
  ADD COLUMN IF NOT EXISTS cluster_id int,
  ADD COLUMN IF NOT EXISTS srid_ambigu boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN bancarisation.plan_cao_calque.srid_source IS
  'EPSG du calque. NULL = on retombe sur plan_cao.srid_declare puis srid_cible.';
COMMENT ON COLUMN bancarisation.plan_cao_calque.srid_ambigu IS
  'True : le calque est réparti sur plusieurs clusters (deux provenances mélangées). À faire trancher, pas à résoudre auto.';

-- 3. calage : reprojection depuis le CRS du calque (srid_direct)
--    ou similitude 2 points sur des coordonnées locales (deux_points / manuel).
CREATE OR REPLACE FUNCTION bancarisation.appliquer_calage_plan(p_plan_id uuid)
RETURNS int
LANGUAGE plpgsql
AS $$
DECLARE
  p record;
  a double precision;
  b double precision;
  d double precision;
  e double precision;
  n int;
BEGIN
  SELECT * INTO p FROM bancarisation.plan_cao WHERE id = p_plan_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'plan_cao % introuvable', p_plan_id;
  END IF;

  a :=  p.echelle * cos(p.rotation_rad);
  b := -p.echelle * sin(p.rotation_rad);
  d :=  p.echelle * sin(p.rotation_rad);
  e :=  p.echelle * cos(p.rotation_rad);

  WITH src AS (
    SELECT pe.id,
           COALESCE(pcc.srid_source, p.srid_declare, p.srid_cible) AS srid_src
    FROM bancarisation.plan_cao_entite pe
    LEFT JOIN bancarisation.plan_cao_calque pcc
           ON pcc.plan_id = pe.plan_id AND pcc.nom = pe.calque
    WHERE pe.plan_id = p_plan_id
  ),
  cale AS (
    SELECT pe.id,
           CASE
             -- CRS connu : geom_local est déjà dans srid_src → reprojection
             WHEN p.calage_mode = 'srid_direct'
                  AND s.srid_src IS NOT NULL
                  AND s.srid_src > 0
             THEN ST_Transform(
                    ST_SetSRID(ST_Force2D(pe.geom_local), s.srid_src),
                    p.srid_cible
                  )
             -- Calage 2 points : similitude vers srid_cible (coords chantier)
             ELSE ST_SetSRID(
                    ST_MakeValid(
                      ST_Affine(ST_Force2D(pe.geom_local), a, b, d, e, p.tx, p.ty)
                    ),
                    p.srid_cible
                  )
           END AS g
    FROM bancarisation.plan_cao_entite pe
    JOIN src s ON s.id = pe.id
  )
  UPDATE bancarisation.plan_cao_entite pe
  SET geom = c.g,
      geom_3857 = ST_Transform(c.g, 3857)
  FROM cale c
  WHERE pe.id = c.id;

  GET DIAGNOSTICS n = ROW_COUNT;
  UPDATE bancarisation.plan_cao
     SET statut = 'cale', modifie_le = now()
   WHERE id = p_plan_id;
  RETURN n;
END
$$;

COMMENT ON FUNCTION bancarisation.appliquer_calage_plan(uuid) IS
  'srid_direct : ST_Transform depuis le CRS du calque. deux_points : ST_Affine (Helmert) vers srid_cible.';

COMMIT;
