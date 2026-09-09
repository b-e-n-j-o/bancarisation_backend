-- =============================================================================
-- Migration 041 — v_ug_parcelles : libellé UG + code métier + projet
-- =============================================================================
-- Foncier : affichage UG (libellé + surface incluse) sans 3 jointures front.
-- À lancer à la main :
--   psql "$DATABASE_URL" -f backend/api/ocr/db/sql/041_v_ug_parcelles_libelle.sql
-- =============================================================================

BEGIN;

DROP VIEW IF EXISTS bancarisation.v_ug_parcelles;

CREATE VIEW bancarisation.v_ug_parcelles AS
  SELECT
    'surf'::text AS ug_type,
    l.ug_id,
    l.parcelle_id,
    l.surface_incluse_m2::numeric AS metrique,
    'm2'::text AS unite,
    COALESCE(NULLIF(trim(u.libelle), ''), u.ug_id) AS libelle,
    u.ug_id AS ug_code,
    u.projet_id
  FROM bancarisation.ug_surf_parcelles l
  JOIN bancarisation.unites_de_gestion_surf u ON u.id = l.ug_id
  UNION ALL
  SELECT
    'lin',
    l.ug_id,
    l.parcelle_id,
    l.longueur_incluse_m,
    'm',
    COALESCE(NULLIF(trim(u.libelle), ''), u.ug_id),
    u.ug_id,
    u.projet_id
  FROM bancarisation.ug_lin_parcelles l
  JOIN bancarisation.unites_de_gestion_lin u ON u.id = l.ug_id
  UNION ALL
  SELECT
    'pct',
    l.ug_id,
    l.parcelle_id,
    NULL::numeric,
    NULL::text,
    COALESCE(NULLIF(trim(u.libelle), ''), u.ug_id),
    u.ug_id,
    u.projet_id
  FROM bancarisation.ug_pct_parcelles l
  JOIN bancarisation.unites_de_gestion_pct u ON u.id = l.ug_id;

GRANT SELECT ON bancarisation.v_ug_parcelles TO anon, authenticated;

-- Recopie les surfaces déjà calculées dans cadastre_parcelle_ug
-- (imports antérieurs laissaient surface_incluse_m2 NULL).
UPDATE bancarisation.ug_surf_parcelles l
SET surface_incluse_m2 = link.surface_inter_m2,
    emprise_partielle = CASE
      WHEN link.surface_inter_m2 IS NOT NULL AND p.contenance_m2 IS NOT NULL
      THEN link.surface_inter_m2 < p.contenance_m2 * 0.98
      ELSE l.emprise_partielle
    END
FROM bancarisation.parcelles p
JOIN bancarisation.unites_de_gestion_surf u ON u.projet_id = p.projet_id
JOIN bancarisation.cadastre_parcelle_ug link
  ON link.projet_id = p.projet_id
 AND link.idu IS NOT DISTINCT FROM p.idu
 AND link.ug_id = u.ug_id
WHERE l.parcelle_id = p.id
  AND l.ug_id = u.id
  AND l.surface_incluse_m2 IS NULL
  AND link.surface_inter_m2 IS NOT NULL;

COMMIT;

NOTIFY pgrst, 'reload schema';
