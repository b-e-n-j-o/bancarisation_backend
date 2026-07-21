-- 009_prestataires.sql
-- Référentiel prestataires (mesures compensatoires).
-- MVP : id + nom. Les attributs futurs sont documentés en commentaires.
--
-- Attributs utiles à prévoir (migrations ultérieures) :
--   siret, forme_juridique
--   adresse, code_postal, commune, departement  → filtre géo / proximité
--   email, telephone, contact_nom
--   specialites text[]   (génie écologique, plantations, suivi faune…)
--   categories_mesure text[]  (EP, TU, TE, SE, MG)
--   actif boolean        (archiver sans supprimer l'historique)
--   notes text
--   organisation_id uuid → multi-tenant si besoin
--
-- Avec des milliers de lignes : ne jamais charger toute la table en
-- menu déroulant — recherche serveurée (ILIKE) côté API.

CREATE TABLE IF NOT EXISTS bancarisation.prestataires (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  nom text NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT prestataires_pkey PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS prestataires_nom_unique_idx
  ON bancarisation.prestataires (lower(trim(nom)));

CREATE INDEX IF NOT EXISTS prestataires_nom_lower_idx
  ON bancarisation.prestataires (lower(nom) text_pattern_ops);

-- Seed synthétique (mesures compensatoires) — idempotent.
INSERT INTO bancarisation.prestataires (id, nom) VALUES
  ('b2000000-0000-4000-8000-000000000001', 'Écosphère Conseil'),
  ('b2000000-0000-4000-8000-000000000002', 'Biotope Occitanie'),
  ('b2000000-0000-4000-8000-000000000003', 'Génie Écologique du Midi'),
  ('b2000000-0000-4000-8000-000000000004', 'Atelier des Haies'),
  ('b2000000-0000-4000-8000-000000000005', 'Naturalia Environnement'),
  ('b2000000-0000-4000-8000-000000000006', 'TerrOïko'),
  ('b2000000-0000-4000-8000-000000000007', 'LPO Mission Conseil'),
  ('b2000000-0000-4000-8000-000000000008', 'Sologne Nature Services'),
  ('b2000000-0000-4000-8000-000000000009', 'AquaTerra Restauration'),
  ('b2000000-0000-4000-8000-00000000000a', 'Paysages & Biodiversité SARL'),
  ('b2000000-0000-4000-8000-00000000000b', 'Faune-Flore Expertise'),
  ('b2000000-0000-4000-8000-00000000000c', 'Compenseo Ingénierie')
ON CONFLICT (id) DO NOTHING;
