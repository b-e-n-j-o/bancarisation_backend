-- 050 — surface d'intersection + pourcentage (corrigé en 051 : % de la parcelle).
-- surface_incluse_m2 = m² d'intersection ; part_ug_pct = % de la parcelle.

ALTER TABLE bancarisation.ug_surf_parcelles
  ADD COLUMN IF NOT EXISTS part_ug_pct numeric;

COMMENT ON COLUMN bancarisation.ug_surf_parcelles.part_ug_pct IS
  'Pourcentage de la superficie de la parcelle concerné par l''intersection avec l''UG.';

UPDATE bancarisation.ug_surf_parcelles l
SET surface_incluse_m2 = link.surface_inter_m2
FROM bancarisation.parcelles p,
     bancarisation.unites_de_gestion_surf u,
     bancarisation.cadastre_parcelle_ug link
WHERE l.parcelle_id = p.id
  AND l.ug_id = u.id
  AND link.projet_id = p.projet_id
  AND link.idu IS NOT DISTINCT FROM p.idu
  AND link.ug_id = u.ug_id
  AND l.surface_incluse_m2 IS NULL
  AND link.surface_inter_m2 IS NOT NULL;

UPDATE bancarisation.ug_surf_parcelles l
SET part_ug_pct = ROUND(
  (
    100.0
    * l.surface_incluse_m2
    / ST_Area(ST_Transform(ST_MakeValid(u.geom_3857), 2154))
  )::numeric,
  2
)
FROM bancarisation.unites_de_gestion_surf u
WHERE u.id = l.ug_id
  AND l.surface_incluse_m2 IS NOT NULL
  AND l.part_ug_pct IS NULL
  AND ST_Area(ST_Transform(ST_MakeValid(u.geom_3857), 2154)) > 0;

CREATE OR REPLACE VIEW bancarisation.v_ug_parcelles AS
  SELECT
    'surf'::text AS ug_type,
    u.ug_id AS ug_id,
    l.parcelle_id,
    sum(l.surface_incluse_m2)::numeric AS metrique,
    'm2'::text AS unite,
    max(COALESCE(NULLIF(trim(u.libelle), ''), u.ug_id)) AS libelle,
    u.ug_id AS ug_code,
    u.projet_id,
    max(l.part_ug_pct)::numeric AS part_ug_pct
  FROM bancarisation.ug_surf_parcelles l
  JOIN bancarisation.unites_de_gestion_surf u ON u.id = l.ug_id
  WHERE u.statut = 'ug' AND u.ug_id IS NOT NULL
  GROUP BY u.projet_id, u.ug_id, l.parcelle_id
  UNION ALL
  SELECT
    'lin',
    u.ug_id,
    l.parcelle_id,
    sum(l.longueur_incluse_m),
    'm',
    max(COALESCE(NULLIF(trim(u.libelle), ''), u.ug_id)),
    u.ug_id,
    u.projet_id,
    NULL::numeric
  FROM bancarisation.ug_lin_parcelles l
  JOIN bancarisation.unites_de_gestion_lin u ON u.id = l.ug_id
  WHERE u.statut = 'ug' AND u.ug_id IS NOT NULL
  GROUP BY u.projet_id, u.ug_id, l.parcelle_id
  UNION ALL
  SELECT
    'pct',
    u.ug_id,
    l.parcelle_id,
    NULL::numeric,
    NULL::text,
    max(COALESCE(NULLIF(trim(u.libelle), ''), u.ug_id)),
    u.ug_id,
    u.projet_id,
    NULL::numeric
  FROM bancarisation.ug_pct_parcelles l
  JOIN bancarisation.unites_de_gestion_pct u ON u.id = l.ug_id
  WHERE u.statut = 'ug' AND u.ug_id IS NOT NULL
  GROUP BY u.projet_id, u.ug_id, l.parcelle_id;

GRANT SELECT ON bancarisation.v_ug_parcelles TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
