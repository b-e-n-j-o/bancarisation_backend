-- 052 — un lien parcelle ↔ code UG (pas un lien par morceau d'UG).
-- La vue additionnait les copies → intersection plus grande que la parcelle.

DELETE FROM bancarisation.ug_surf_parcelles l
USING bancarisation.unites_de_gestion_surf u
WHERE l.ug_id = u.id
  AND l.id <> (
    SELECT l2.id
    FROM bancarisation.ug_surf_parcelles l2
    JOIN bancarisation.unites_de_gestion_surf u2 ON u2.id = l2.ug_id
    WHERE l2.parcelle_id = l.parcelle_id AND u2.ug_id = u.ug_id
    ORDER BY l2.surface_incluse_m2 DESC NULLS LAST, l2.id
    LIMIT 1
  );

DELETE FROM bancarisation.ug_lin_parcelles l
USING bancarisation.unites_de_gestion_lin u
WHERE l.ug_id = u.id
  AND l.id <> (
    SELECT l2.id
    FROM bancarisation.ug_lin_parcelles l2
    JOIN bancarisation.unites_de_gestion_lin u2 ON u2.id = l2.ug_id
    WHERE l2.parcelle_id = l.parcelle_id AND u2.ug_id = u.ug_id
    ORDER BY l2.id
    LIMIT 1
  );

DELETE FROM bancarisation.ug_pct_parcelles l
USING bancarisation.unites_de_gestion_pct u
WHERE l.ug_id = u.id
  AND l.id <> (
    SELECT l2.id
    FROM bancarisation.ug_pct_parcelles l2
    JOIN bancarisation.unites_de_gestion_pct u2 ON u2.id = l2.ug_id
    WHERE l2.parcelle_id = l.parcelle_id AND u2.ug_id = u.ug_id
    ORDER BY l2.id
    LIMIT 1
  );

CREATE OR REPLACE VIEW bancarisation.v_ug_parcelles AS
  SELECT
    'surf'::text AS ug_type,
    u.ug_id AS ug_id,
    l.parcelle_id,
    max(l.surface_incluse_m2)::numeric AS metrique,
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
    max(l.longueur_incluse_m),
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
