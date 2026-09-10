-- =============================================================================
-- Migration 043 — Contacts + responsable d'action
-- =============================================================================
-- Référentiel de rôles, extension de `personnes` (déjà créée par le foncier),
-- liens projet ↔ personne, responsable sur occurrence, vues.
-- `prestataires` et le canal DREAL ne sont pas touchés.
--
-- À lancer à la main (psql absent) :
--   python3 depuis backend/ via psycopg + get_database_url
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Référentiel de rôles (le rôle est porté au projet, pas à la personne)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bancarisation.role_contact_ref (
  code       text PRIMARY KEY,
  libelle    text NOT NULL,
  categorie  text NOT NULL,
  ordre      integer NOT NULL DEFAULT 0,
  CONSTRAINT role_contact_ref_categorie_chk CHECK (categorie IN (
    'maitrise_ouvrage', 'operateur', 'foncier', 'prestataire',
    'controle', 'collectivite', 'tiers'
  ))
);

INSERT INTO bancarisation.role_contact_ref (code, libelle, categorie, ordre) VALUES
  -- Maîtrise d'ouvrage
  ('maitre_ouvrage',          'Maître d''ouvrage',              'maitrise_ouvrage', 10),
  ('maitre_ouvrage_delegue',  'Maître d''ouvrage délégué',      'maitrise_ouvrage', 11),
  ('charge_mission_moa',      'Chargé de mission MOA',          'maitrise_ouvrage', 12),
  -- Opérateur
  ('operateur_compensation',  'Opérateur de compensation',      'operateur',        20),
  ('charge_projet',           'Chargé de projet',               'operateur',        21),
  ('ecologue_referent',       'Écologue référent',              'operateur',        22),
  -- Foncier
  ('proprietaire',            'Propriétaire',                   'foncier',          30),
  ('preneur_bail',            'Preneur à bail',                 'foncier',          31),
  ('gestionnaire_site',       'Gestionnaire de site',           'foncier',          32),
  ('safer',                   'SAFER',                          'foncier',          33),
  ('notaire',                 'Notaire',                        'foncier',          34),
  -- Prestataires (interlocuteurs personnes — la table prestataires reste)
  ('prestataire_travaux',     'Prestataire travaux',            'prestataire',      40),
  ('prestataire_suivi',       'Prestataire de suivi',           'prestataire',      41),
  ('bureau_etudes',           'Bureau d''études',               'prestataire',      42),
  -- Contrôle / État
  ('dreal',                   'DREAL',                          'controle',         50),
  ('ddt',                     'DDT / DDTM',                     'controle',         51),
  ('ofb',                     'OFB',                            'controle',         52),
  ('csrpn',                   'CSRPN',                          'controle',         53),
  -- Collectivités
  ('commune',                 'Commune',                        'collectivite',     60),
  ('intercommunalite',        'Intercommunalité',               'collectivite',     61),
  ('departement',             'Département',                    'collectivite',     62),
  ('region',                  'Région',                         'collectivite',     63),
  -- Tiers
  ('association',             'Association',                    'tiers',            70),
  ('riverain',                'Riverain',                       'tiers',            71),
  ('autre',                   'Autre',                          'tiers',            79)
ON CONFLICT (code) DO UPDATE
SET libelle   = EXCLUDED.libelle,
    categorie = EXCLUDED.categorie,
    ordre     = EXCLUDED.ordre;

-- ---------------------------------------------------------------------------
-- 2. Extension de `personnes` (colonnes absentes seulement)
--    `nom` sert déjà physique et morale — pas de raison_sociale.
--    `notes` existe déjà — pas de colonne commentaire.
-- ---------------------------------------------------------------------------

ALTER TABLE bancarisation.personnes
  ADD COLUMN IF NOT EXISTS structure_id   uuid,
  ADD COLUMN IF NOT EXISTS prestataire_id uuid,
  ADD COLUMN IF NOT EXISTS fonction       text,
  ADD COLUMN IF NOT EXISTS telephone_2    text,
  ADD COLUMN IF NOT EXISTS utilisateur_id uuid,
  ADD COLUMN IF NOT EXISTS archive        boolean NOT NULL DEFAULT false;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'personnes_structure_fkey'
  ) THEN
    ALTER TABLE bancarisation.personnes
      ADD CONSTRAINT personnes_structure_fkey
      FOREIGN KEY (structure_id) REFERENCES bancarisation.personnes(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'personnes_prestataire_fkey'
  ) THEN
    ALTER TABLE bancarisation.personnes
      ADD CONSTRAINT personnes_prestataire_fkey
      FOREIGN KEY (prestataire_id) REFERENCES bancarisation.prestataires(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'personnes_utilisateur_fkey'
  ) THEN
    ALTER TABLE bancarisation.personnes
      ADD CONSTRAINT personnes_utilisateur_fkey
      FOREIGN KEY (utilisateur_id) REFERENCES bancarisation.membre(user_id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS personnes_utilisateur_id_uidx
  ON bancarisation.personnes (utilisateur_id)
  WHERE utilisateur_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS personnes_structure_id_idx
  ON bancarisation.personnes (structure_id);

CREATE INDEX IF NOT EXISTS personnes_prestataire_id_idx
  ON bancarisation.personnes (prestataire_id);

CREATE INDEX IF NOT EXISTS personnes_archive_idx
  ON bancarisation.personnes (archive);

COMMENT ON COLUMN bancarisation.personnes.structure_id IS
  'Personne morale de rattachement (auto-FK).';
COMMENT ON COLUMN bancarisation.personnes.utilisateur_id IS
  'Compte démo / membre.user_id (header X-User-Id). Un seul lien par compte.';
COMMENT ON COLUMN bancarisation.personnes.archive IS
  'Archivage logique — pas de suppression physique.';

-- Identité démo pour le sélecteur « Je suis… » (opérateur seed 018).
INSERT INTO bancarisation.personnes (
  organisation_id, type_personne, nom, prenom, email, fonction, utilisateur_id
)
SELECT
  m.organisation_id,
  'physique',
  'Dupont',
  'Marion',
  'marion.dupont@example.org',
  'Chargée de projet',
  m.user_id
FROM bancarisation.membre m
WHERE m.user_id = 'c1000000-0000-0000-0000-000000000002'
  AND m.organisation_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM bancarisation.personnes p WHERE p.utilisateur_id = m.user_id
  );

-- ---------------------------------------------------------------------------
-- 3. Lien projet ↔ personne (rôle au projet)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bancarisation.projet_contact (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
  personne_id     uuid NOT NULL REFERENCES bancarisation.personnes(id) ON DELETE CASCADE,
  role_code       text NOT NULL REFERENCES bancarisation.role_contact_ref(code),
  role_precision  text,
  principal       boolean NOT NULL DEFAULT false,
  date_debut      date,
  date_fin        date,
  commentaire     text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT projet_contact_unique UNIQUE (projet_id, personne_id, role_code)
);

CREATE INDEX IF NOT EXISTS projet_contact_projet_idx
  ON bancarisation.projet_contact (projet_id);
CREATE INDEX IF NOT EXISTS projet_contact_personne_idx
  ON bancarisation.projet_contact (personne_id);

COMMENT ON TABLE bancarisation.projet_contact IS
  'Intervenant d''un projet : une même personne peut avoir plusieurs rôles, sur plusieurs projets.';

ALTER TABLE bancarisation.projet_contact ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS projet_contact_lecture ON bancarisation.projet_contact;
CREATE POLICY projet_contact_lecture ON bancarisation.projet_contact
  FOR SELECT
  USING (
    bancarisation.peut_lire_projet(projet_id)
    OR auth.uid() IS NULL
  );

DROP POLICY IF EXISTS projet_contact_ecriture ON bancarisation.projet_contact;
CREATE POLICY projet_contact_ecriture ON bancarisation.projet_contact
  FOR ALL
  USING (
    bancarisation.peut_ecrire_projet(projet_id)
    OR auth.uid() IS NULL
  )
  WITH CHECK (
    bancarisation.peut_ecrire_projet(projet_id)
    OR auth.uid() IS NULL
  );

-- ---------------------------------------------------------------------------
-- 4. Responsable d'action
-- ---------------------------------------------------------------------------

ALTER TABLE bancarisation.occurrence
  ADD COLUMN IF NOT EXISTS responsable_id uuid;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'occurrence_responsable_fkey'
  ) THEN
    ALTER TABLE bancarisation.occurrence
      ADD CONSTRAINT occurrence_responsable_fkey
      FOREIGN KEY (responsable_id) REFERENCES bancarisation.personnes(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS occurrence_responsable_idx
  ON bancarisation.occurrence (responsable_id);

COMMENT ON COLUMN bancarisation.occurrence.responsable_id IS
  'Personne responsable de l''action. Hors trigger log_budget_mouvement.';

-- ---------------------------------------------------------------------------
-- 5. v_occurrence_calendrier : colonnes nouvelles EN FIN uniquement
--    DROP de v_action_portefeuille d'abord (vc.* figé à la création).
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS bancarisation.v_action_portefeuille;

CREATE OR REPLACE VIEW bancarisation.v_occurrence_calendrier
WITH (security_invoker = true) AS
 SELECT o.id,
    o.projet_id,
    o.echeance_id,
    o.annee,
    o.code,
    o.titre,
    o.categorie,
    o.statut,
    o.ug_ids,
    o.mois_debut,
    o.mois_fin,
    o.traverse_nouvel_an,
    o.origine,
    o.confiance,
    o.champs_a_confirmer,
    o.avertissements,
    o.modifie_le,
    o.date_realisation,
    o.commentaire,
    e.cle AS echeance_cle,
    e.code_operation,
    e.libelle AS echeance_libelle,
    e.action_cle,
    COALESCE(NULLIF(NULLIF(o.lib_thema, ''::text), 'autre'::text), NULLIF(NULLIF(e.lib_thema, ''::text), 'autre'::text), NULLIF(af.lib_thema, ''::text), 'autre'::text) AS lib_thema,
    o.montant_ht,
    o.montant_ttc,
    o.taux_tva,
    o.prestataire,
    o.ligne_budget_id,
    o.montant_initial,
    o.annee_initiale,
    o.montant_engage,
    o.montant_realise,
    o.prestataire_id,
    COALESCE(p.nom, o.prestataire) AS prestataire_nom,
    e.recurrence AS echeance_recurrence,
    o.date_realisation_fin,
    o.surface_m2,
    o.responsable_id,
    COALESCE(NULLIF(trim(concat_ws(' ', resp.prenom, resp.nom)), ''), resp.nom) AS responsable_nom
   FROM bancarisation.occurrence o
     LEFT JOIN bancarisation.echeance e ON e.id = o.echeance_id
     LEFT JOIN bancarisation.action_fiche af ON af.projet_id = o.projet_id AND af.cle = e.action_cle
     LEFT JOIN bancarisation.prestataires p ON p.id = o.prestataire_id
     LEFT JOIN bancarisation.personnes resp ON resp.id = o.responsable_id;

CREATE OR REPLACE VIEW bancarisation.v_action_portefeuille
WITH (security_invoker = true) AS
SELECT
  vc.*,
  p.nom               AS projet_nom,
  p.reference_interne AS projet_reference,
  p.departement       AS projet_departement,
  p.organisation_id,
  org.nom             AS organisation_nom,
  fen.date_fin_fenetre,
  (vc.statut NOT IN ('realise', 'supprime')
   AND fen.date_fin_fenetre < current_date) AS en_retard
FROM bancarisation.v_occurrence_calendrier vc
JOIN bancarisation.projets p              ON p.id = vc.projet_id
LEFT JOIN bancarisation.organisations org ON org.id = p.organisation_id
CROSS JOIN LATERAL (
  SELECT (
    make_date(
      vc.annee + CASE WHEN coalesce(vc.traverse_nouvel_an, false) THEN 1 ELSE 0 END,
      coalesce(vc.mois_fin, 12),
      1
    ) + interval '1 month' - interval '1 day'
  )::date AS date_fin_fenetre
) fen;

-- ---------------------------------------------------------------------------
-- 6. v_contact_projet : saisie manuelle ∪ foncier (lecture seule)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW bancarisation.v_contact_projet
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

-- ---------------------------------------------------------------------------
-- 7. Grants
-- ---------------------------------------------------------------------------

GRANT SELECT ON bancarisation.role_contact_ref TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON bancarisation.projet_contact TO anon, authenticated;
GRANT SELECT ON bancarisation.v_occurrence_calendrier TO anon, authenticated;
GRANT SELECT ON bancarisation.v_action_portefeuille TO anon, authenticated;
GRANT SELECT ON bancarisation.v_contact_projet TO anon, authenticated;

COMMIT;

NOTIFY pgrst, 'reload schema';
