-- =============================================================================
-- Migration 049 — Une ligne = une entité déposée (plus une UG).
-- =============================================================================
-- Les colonnes statut / couche / depot_id / … sont déjà en base sur certains
-- environnements. Ce script est idempotent : ALTER IF NOT EXISTS, contraintes
-- créées seulement si absentes, vues remplacées.
--
--   psql "$DATABASE_URL" -f backend/api/ocr/db/sql/049_geometries_entites.sql
-- =============================================================================

BEGIN;

-- ── Colonnes d'entité (surf / lin / pct) ────────────────────────────────────

ALTER TABLE bancarisation.unites_de_gestion_surf
  ALTER COLUMN ug_id DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS statut text NOT NULL DEFAULT 'ug',
  ADD COLUMN IF NOT EXISTS couche text,
  ADD COLUMN IF NOT EXISTS index_entite int,
  ADD COLUMN IF NOT EXISTS depot_id text,
  ADD COLUMN IF NOT EXISTS zone_id text,
  ADD COLUMN IF NOT EXISTS motif text,
  ADD COLUMN IF NOT EXISTS origine text NOT NULL DEFAULT 'ia',
  ADD COLUMN IF NOT EXISTS confiance text,
  ADD COLUMN IF NOT EXISTS modifie_le timestamptz;

ALTER TABLE bancarisation.unites_de_gestion_lin
  ALTER COLUMN ug_id DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS statut text NOT NULL DEFAULT 'ug',
  ADD COLUMN IF NOT EXISTS couche text,
  ADD COLUMN IF NOT EXISTS index_entite int,
  ADD COLUMN IF NOT EXISTS depot_id text,
  ADD COLUMN IF NOT EXISTS zone_id text,
  ADD COLUMN IF NOT EXISTS motif text,
  ADD COLUMN IF NOT EXISTS origine text NOT NULL DEFAULT 'ia',
  ADD COLUMN IF NOT EXISTS confiance text,
  ADD COLUMN IF NOT EXISTS modifie_le timestamptz;

ALTER TABLE bancarisation.unites_de_gestion_pct
  ALTER COLUMN ug_id DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS statut text NOT NULL DEFAULT 'ug',
  ADD COLUMN IF NOT EXISTS couche text,
  ADD COLUMN IF NOT EXISTS index_entite int,
  ADD COLUMN IF NOT EXISTS depot_id text,
  ADD COLUMN IF NOT EXISTS zone_id text,
  ADD COLUMN IF NOT EXISTS motif text,
  ADD COLUMN IF NOT EXISTS origine text NOT NULL DEFAULT 'ia',
  ADD COLUMN IF NOT EXISTS confiance text,
  ADD COLUMN IF NOT EXISTS modifie_le timestamptz;

DO $$
DECLARE
  t text;
  chk_statut text;
  chk_origine text;
  chk_coherent text;
BEGIN
  FOREACH t IN ARRAY ARRAY['surf', 'lin', 'pct'] LOOP
    chk_statut := format('unites_de_gestion_%s_statut_check', t);
    chk_origine := format('unites_de_gestion_%s_origine_check', t);
    chk_coherent := format('ug_%s_statut_coherent', t);

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = chk_statut) THEN
      EXECUTE format(
        'ALTER TABLE bancarisation.unites_de_gestion_%s
           ADD CONSTRAINT %I CHECK (statut IN (''ug'', ''contexte'', ''non_affectee'', ''ecartee''))',
        t, chk_statut
      );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = chk_origine) THEN
      EXECUTE format(
        'ALTER TABLE bancarisation.unites_de_gestion_%s
           ADD CONSTRAINT %I CHECK (origine IN (''ia'', ''user''))',
        t, chk_origine
      );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = chk_coherent) THEN
      EXECUTE format(
        'ALTER TABLE bancarisation.unites_de_gestion_%s
           ADD CONSTRAINT %I CHECK ((statut = ''ug'') = (ug_id IS NOT NULL))',
        t, chk_coherent
      );
    END IF;

    EXECUTE format(
      'CREATE INDEX IF NOT EXISTS unites_de_gestion_%s_statut_idx
         ON bancarisation.unites_de_gestion_%s (projet_id, statut)',
      t, t
    );
    EXECUTE format(
      'CREATE INDEX IF NOT EXISTS unites_de_gestion_%s_depot_idx
         ON bancarisation.unites_de_gestion_%s (projet_id, depot_id)',
      t, t
    );
  END LOOP;
END $$;

-- ── Vues UG (union des géométries, une ligne par code) ──────────────────────
-- CREATE OR REPLACE exige le même ordre de colonnes que les vues déjà en base
-- (projet_id, ug_id, libelle, geom, geom_3857, nb_entites). description en dernier.

CREATE OR REPLACE VIEW bancarisation.v_ug_surf AS
SELECT projet_id, ug_id,
       max(libelle)        AS libelle,
       ST_Union(geom)      AS geom,
       ST_Union(geom_3857) AS geom_3857,
       count(*)            AS nb_entites,
       max(description)    AS description
FROM bancarisation.unites_de_gestion_surf
WHERE statut = 'ug'
GROUP BY projet_id, ug_id;

CREATE OR REPLACE VIEW bancarisation.v_ug_lin AS
SELECT projet_id, ug_id,
       max(libelle)        AS libelle,
       ST_Union(geom)      AS geom,
       ST_Union(geom_3857) AS geom_3857,
       count(*)            AS nb_entites,
       max(description)    AS description
FROM bancarisation.unites_de_gestion_lin
WHERE statut = 'ug'
GROUP BY projet_id, ug_id;

CREATE OR REPLACE VIEW bancarisation.v_ug_pct AS
SELECT projet_id, ug_id,
       max(libelle)        AS libelle,
       ST_Union(geom)      AS geom,
       ST_Union(geom_3857) AS geom_3857,
       count(*)            AS nb_entites,
       max(description)    AS description
FROM bancarisation.unites_de_gestion_pct
WHERE statut = 'ug'
GROUP BY projet_id, ug_id;

GRANT SELECT ON bancarisation.v_ug_surf, bancarisation.v_ug_lin, bancarisation.v_ug_pct
  TO anon, authenticated;

-- ── Historique append-only (modèle budget_mouvement) ────────────────────────

CREATE TABLE IF NOT EXISTS bancarisation.geometrie_mouvement (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projet_id      uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
  table_source   text NOT NULL CHECK (table_source IN ('surf', 'lin', 'pct')),
  entite_id      uuid NOT NULL,
  ancien_statut  text,
  nouveau_statut text,
  ancien_ug_id   text,
  nouveau_ug_id  text,
  motif          text,
  auteur         text,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS geometrie_mouvement_projet_idx
  ON bancarisation.geometrie_mouvement (projet_id, created_at DESC);
CREATE INDEX IF NOT EXISTS geometrie_mouvement_entite_idx
  ON bancarisation.geometrie_mouvement (table_source, entite_id, created_at DESC);

-- ── Couche sans CRS identifiable ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bancarisation.couche_non_georeferencee (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projet_id      uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
  depot_id       text NOT NULL,
  couche         text NOT NULL,
  fichier        text NOT NULL,
  document_id    uuid REFERENCES bancarisation.documents(id) ON DELETE SET NULL,
  nb_entites     int,
  epsg_propose   int,
  statut         text NOT NULL DEFAULT 'en_attente_epsg'
                 CHECK (statut IN ('en_attente_epsg', 'reprise', 'echec')),
  motif          text DEFAULT 'couche non géoréférencée',
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (projet_id, depot_id, couche)
);

CREATE INDEX IF NOT EXISTS couche_non_georeferencee_projet_idx
  ON bancarisation.couche_non_georeferencee (projet_id, depot_id);

-- ── Foncier : regrouper par (projet_id, ug_id) ──────────────────────────────
-- v_contact_projet dépend de v_ug_parcelles : on la droppe, on recrée
-- v_ug_parcelles (ug_id passe d'uuid entité à code UG), puis on restaure
-- v_contact_projet (définition 046, sans le seed).

DROP VIEW IF EXISTS bancarisation.v_contact_projet;
DROP VIEW IF EXISTS bancarisation.v_ug_parcelles;

CREATE VIEW bancarisation.v_ug_parcelles AS
  SELECT
    'surf'::text AS ug_type,
    u.ug_id AS ug_id,
    l.parcelle_id,
    sum(l.surface_incluse_m2)::numeric AS metrique,
    'm2'::text AS unite,
    max(COALESCE(NULLIF(trim(u.libelle), ''), u.ug_id)) AS libelle,
    u.ug_id AS ug_code,
    u.projet_id
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
    u.projet_id
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
    u.projet_id
  FROM bancarisation.ug_pct_parcelles l
  JOIN bancarisation.unites_de_gestion_pct u ON u.id = l.ug_id
  WHERE u.statut = 'ug' AND u.ug_id IS NOT NULL
  GROUP BY u.projet_id, u.ug_id, l.parcelle_id;

GRANT SELECT ON bancarisation.v_ug_parcelles TO anon, authenticated;

CREATE VIEW bancarisation.v_contact_projet
WITH (security_invoker = true) AS
WITH foncier_liens AS (
  SELECT DISTINCT
    COALESCE(ug.projet_id, pa.projet_id) AS projet_id,
    df.titulaire_id AS personne_id,
    CASE
      WHEN df.nature IN ('propriete', 'usufruit', 'nue_propriete') THEN 'proprietaire'
      WHEN df.nature IN (
        'bail_rural', 'bail_rural_clauses_env', 'bail_emphyteotique',
        'commodat', 'aot', 'convention_paturage'
      ) THEN 'preneur_bail'
      WHEN df.nature IN ('ore', 'convention_gestion', 'regime_forestier')
        THEN 'gestionnaire_site'
      WHEN df.nature = 'cmd_safer' THEN 'safer'
      ELSE 'autre'
    END AS role_code,
    df.nature AS role_precision
  FROM bancarisation.droits_fonciers df
  JOIN bancarisation.parcelles pa ON pa.id = df.parcelle_id
  LEFT JOIN bancarisation.v_ug_parcelles ug ON ug.parcelle_id = pa.id
  WHERE df.titulaire_id IS NOT NULL
    AND COALESCE(ug.projet_id, pa.projet_id) IS NOT NULL

  UNION

  SELECT DISTINCT
    COALESCE(ug.projet_id, pa.projet_id),
    df.constituant_id,
    'proprietaire',
    df.nature
  FROM bancarisation.droits_fonciers df
  JOIN bancarisation.parcelles pa ON pa.id = df.parcelle_id
  LEFT JOIN bancarisation.v_ug_parcelles ug ON ug.parcelle_id = pa.id
  WHERE df.constituant_id IS NOT NULL
    AND df.constituant_id IS DISTINCT FROM df.titulaire_id
    AND COALESCE(ug.projet_id, pa.projet_id) IS NOT NULL
)
SELECT
  pc.id AS lien_id,
  pc.projet_id,
  pr.nom AS projet_nom,
  pc.personne_id,
  pc.role_code,
  r.libelle AS role_libelle,
  r.categorie AS role_categorie,
  r.ordre AS role_ordre,
  pc.role_precision,
  pc.principal,
  pc.date_debut,
  pc.date_fin,
  pc.commentaire,
  'manuel'::text AS source,
  COALESCE(NULLIF(trim(concat_ws(' ', pe.prenom, pe.nom)), ''), pe.nom) AS personne_nom,
  pe.prenom,
  pe.nom,
  pe.email,
  pe.telephone,
  pe.telephone_2,
  pe.fonction,
  pe.type_personne,
  pe.structure_id,
  COALESCE(NULLIF(trim(concat_ws(' ', st.prenom, st.nom)), ''), st.nom) AS structure_nom,
  pe.prestataire_id,
  pe.utilisateur_id,
  pe.archive
FROM bancarisation.projet_contact pc
JOIN bancarisation.role_contact_ref r ON r.code = pc.role_code
JOIN bancarisation.personnes pe ON pe.id = pc.personne_id
LEFT JOIN bancarisation.projets pr ON pr.id = pc.projet_id
LEFT JOIN bancarisation.personnes st ON st.id = pe.structure_id

UNION ALL

SELECT
  NULL::uuid,
  fl.projet_id,
  pr.nom,
  fl.personne_id,
  fl.role_code,
  r.libelle,
  r.categorie,
  r.ordre,
  fl.role_precision,
  false,
  NULL::date,
  NULL::date,
  NULL::text,
  'foncier'::text,
  COALESCE(NULLIF(trim(concat_ws(' ', pe.prenom, pe.nom)), ''), pe.nom),
  pe.prenom,
  pe.nom,
  pe.email,
  pe.telephone,
  pe.telephone_2,
  pe.fonction,
  pe.type_personne,
  pe.structure_id,
  COALESCE(NULLIF(trim(concat_ws(' ', st.prenom, st.nom)), ''), st.nom),
  pe.prestataire_id,
  pe.utilisateur_id,
  pe.archive
FROM foncier_liens fl
JOIN bancarisation.role_contact_ref r ON r.code = fl.role_code
JOIN bancarisation.personnes pe ON pe.id = fl.personne_id
LEFT JOIN bancarisation.projets pr ON pr.id = fl.projet_id
LEFT JOIN bancarisation.personnes st ON st.id = pe.structure_id;

GRANT SELECT ON bancarisation.v_contact_projet TO anon, authenticated;

COMMENT ON VIEW bancarisation.v_ug_surf IS
  'Une ligne par UG surfacique : union des entités statut=ug.';
COMMENT ON VIEW bancarisation.v_ug_lin IS
  'Une ligne par UG linéaire : union des entités statut=ug.';
COMMENT ON VIEW bancarisation.v_ug_pct IS
  'Une ligne par UG ponctuelle : union des entités statut=ug.';
COMMENT ON TABLE bancarisation.geometrie_mouvement IS
  'Journal append-only des changements de rattachement d''une entité géométrique.';
COMMENT ON TABLE bancarisation.couche_non_georeferencee IS
  'Couche SIG déposée sans CRS : fichier en bucket, EPSG à saisir pour relancer l''écriture.';

COMMIT;

NOTIFY pgrst, 'reload schema';
