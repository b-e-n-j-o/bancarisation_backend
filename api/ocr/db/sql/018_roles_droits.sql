-- 018_roles_droits.sql
-- Socle d'habilitation : qui voit quoi.
--
-- Trois rôles :
--   'controleur' — agent de l'État (DREAL/DDT) : lecture sur TOUT le parc,
--                  aucune écriture sur les données métier des organisations.
--   'operateur'  — membre d'une organisation (bureau d'études, maître
--                  d'ouvrage) : lecture + écriture sur SES projets uniquement.
--   'admin'      — exploitation de la plateforme.
--
-- Un utilisateur 'operateur' est rattaché à une organisation ; un
-- 'controleur' ne l'est pas (organisation_id NULL).

CREATE TABLE IF NOT EXISTS bancarisation.membre (
  user_id         uuid NOT NULL,          -- = auth.users.id (Supabase)
  organisation_id uuid NULL,
  role            text NOT NULL,
  actif           boolean NOT NULL DEFAULT true,
  created_at      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT membre_pkey PRIMARY KEY (user_id),
  CONSTRAINT membre_organisation_fkey FOREIGN KEY (organisation_id)
    REFERENCES bancarisation.organisations (id) ON DELETE CASCADE,
  CONSTRAINT membre_role_chk CHECK (role IN ('controleur', 'operateur', 'admin')),
  -- Un opérateur DOIT avoir une organisation ; un contrôleur n'en a pas.
  CONSTRAINT membre_org_coherence_chk CHECK (
    (role = 'operateur' AND organisation_id IS NOT NULL)
    OR (role <> 'operateur')
  )
);

CREATE INDEX IF NOT EXISTS membre_organisation_idx
  ON bancarisation.membre (organisation_id);

-- Seed démo (identités stables pour tests API via header X-User-Id)
INSERT INTO bancarisation.membre (user_id, organisation_id, role) VALUES
  ('c1000000-0000-0000-0000-000000000001', NULL, 'controleur'),
  ('c1000000-0000-0000-0000-000000000002', 'a1000000-0000-0000-0000-000000000001', 'operateur'),
  ('c1000000-0000-0000-0000-000000000099', NULL, 'admin')
ON CONFLICT (user_id) DO NOTHING;

-- ---------------------------------------------------------------
-- Fonctions d'habilitation.
-- STABLE + SECURITY DEFINER : utilisables dans les politiques RLS sans
-- provoquer de récursion sur la table membre elle-même.
-- ---------------------------------------------------------------

CREATE OR REPLACE FUNCTION bancarisation.role_courant()
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = bancarisation, pg_temp
AS $$
  SELECT role FROM bancarisation.membre
  WHERE user_id = auth.uid() AND actif
$$;

CREATE OR REPLACE FUNCTION bancarisation.organisation_courante()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = bancarisation, pg_temp
AS $$
  SELECT organisation_id FROM bancarisation.membre
  WHERE user_id = auth.uid() AND actif
$$;

CREATE OR REPLACE FUNCTION bancarisation.peut_lire_projet(p_projet_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = bancarisation, pg_temp
AS $$
  SELECT
    bancarisation.role_courant() IN ('controleur', 'admin')
    OR EXISTS (
      SELECT 1 FROM bancarisation.projets p
      WHERE p.id = p_projet_id
        AND p.organisation_id = bancarisation.organisation_courante()
    )
$$;

CREATE OR REPLACE FUNCTION bancarisation.peut_ecrire_projet(p_projet_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = bancarisation, pg_temp
AS $$
  SELECT
    bancarisation.role_courant() = 'admin'
    OR EXISTS (
      SELECT 1 FROM bancarisation.projets p
      WHERE p.id = p_projet_id
        AND p.organisation_id = bancarisation.organisation_courante()
        AND bancarisation.role_courant() = 'operateur'
    )
$$;

-- ---------------------------------------------------------------
-- RLS. À activer table par table.
-- ⚠️ La RLS ne protège QUE les connexions porteuses d'un JWT utilisateur
--    (chemin Supabase REST / PostgREST). Les connexions psycopg du backend
--    utilisent un rôle privilégié qui la contourne : le filtrage par
--    organisation DOIT AUSSI être écrit dans les requêtes de l'API.
-- ---------------------------------------------------------------

ALTER TABLE bancarisation.projets        ENABLE ROW LEVEL SECURITY;
ALTER TABLE bancarisation.occurrence     ENABLE ROW LEVEL SECURITY;
ALTER TABLE bancarisation.rapport_bilan  ENABLE ROW LEVEL SECURITY;
ALTER TABLE bancarisation.budget_mouvement ENABLE ROW LEVEL SECURITY;
ALTER TABLE bancarisation.budget_baseline  ENABLE ROW LEVEL SECURITY;
ALTER TABLE bancarisation.ligne_budget     ENABLE ROW LEVEL SECURITY;
ALTER TABLE bancarisation.organisations    ENABLE ROW LEVEL SECURITY;

-- projets
DROP POLICY IF EXISTS projets_lecture ON bancarisation.projets;
CREATE POLICY projets_lecture ON bancarisation.projets FOR SELECT
  USING (
    bancarisation.role_courant() IN ('controleur', 'admin')
    OR organisation_id = bancarisation.organisation_courante()
  );

DROP POLICY IF EXISTS projets_ecriture ON bancarisation.projets;
CREATE POLICY projets_ecriture ON bancarisation.projets FOR ALL
  USING (bancarisation.peut_ecrire_projet(id))
  WITH CHECK (bancarisation.peut_ecrire_projet(id));

-- Tables filles : même logique via projet_id.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'occurrence', 'rapport_bilan', 'budget_mouvement',
    'budget_baseline', 'ligne_budget'
  ] LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS %I_lecture ON bancarisation.%I', t, t);
    EXECUTE format(
      'CREATE POLICY %I_lecture ON bancarisation.%I FOR SELECT
         USING (bancarisation.peut_lire_projet(projet_id))', t, t);
    EXECUTE format(
      'DROP POLICY IF EXISTS %I_ecriture ON bancarisation.%I', t, t);
    EXECUTE format(
      'CREATE POLICY %I_ecriture ON bancarisation.%I FOR ALL
         USING (bancarisation.peut_ecrire_projet(projet_id))
         WITH CHECK (bancarisation.peut_ecrire_projet(projet_id))', t, t);
  END LOOP;
END $$;

-- organisations : un contrôleur voit toutes les organisations (nécessaire
-- pour la vue comparative) ; un opérateur ne voit que la sienne.
DROP POLICY IF EXISTS organisations_lecture ON bancarisation.organisations;
CREATE POLICY organisations_lecture ON bancarisation.organisations FOR SELECT
  USING (
    bancarisation.role_courant() IN ('controleur', 'admin')
    OR id = bancarisation.organisation_courante()
  );

-- ---------------------------------------------------------------
-- Seuils de signaux : paramétrables, car ce sont des choix quasi
-- réglementaires et non techniques. Une DREAL doit pouvoir les ajuster
-- sans redéploiement.
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bancarisation.parametre_signal (
  code        text NOT NULL,
  valeur      numeric NOT NULL,
  libelle     text NOT NULL,
  maj_le      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT parametre_signal_pkey PRIMARY KEY (code)
);

INSERT INTO bancarisation.parametre_signal (code, valeur, libelle) VALUES
  ('bilan_manquant_critique',   2,    'Nombre de bilans non remis à partir duquel le signal est critique'),
  ('retard_critique_ans',       2,    'Ancienneté (années) d''un retard d''exécution le rendant critique'),
  ('sous_conso_seuil',          0.60, 'Taux d''exécution sur exercices échus en deçà duquel alerter'),
  ('sous_conso_critique',       0.40, 'Taux d''exécution rendant le signal critique'),
  ('derive_seuil_pct',          0.10, 'Dérive budgétaire relative à partir de laquelle alerter'),
  ('derive_critique_pct',       0.25, 'Dérive budgétaire relative rendant le signal critique'),
  ('silence_mois',              12,   'Absence d''activité (mois) déclenchant le signal'),
  ('silence_critique_mois',     24,   'Absence d''activité (mois) rendant le signal critique')
ON CONFLICT (code) DO NOTHING;

CREATE OR REPLACE FUNCTION bancarisation.seuil(p_code text)
RETURNS numeric
LANGUAGE sql
STABLE
AS $$
  SELECT valeur FROM bancarisation.parametre_signal WHERE code = p_code
$$;
