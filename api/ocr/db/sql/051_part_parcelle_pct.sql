-- 051 — part_ug_pct = % de la parcelle (pas de l'UG) concerné par l'intersection.

COMMENT ON COLUMN bancarisation.ug_surf_parcelles.part_ug_pct IS
  'Pourcentage de la superficie de la parcelle concerné par l''intersection avec l''UG.';

UPDATE bancarisation.ug_surf_parcelles l
SET part_ug_pct = ROUND(
  (
    100.0
    * l.surface_incluse_m2
    / COALESCE(
        NULLIF(p.surface_calculee_m2, 0),
        NULLIF(p.contenance_m2, 0),
        ST_Area(p.geom)
      )
  )::numeric,
  2
)
FROM bancarisation.parcelles p
WHERE l.parcelle_id = p.id
  AND l.surface_incluse_m2 IS NOT NULL
  AND COALESCE(
    NULLIF(p.surface_calculee_m2, 0),
    NULLIF(p.contenance_m2, 0),
    ST_Area(p.geom)
  ) > 0;
