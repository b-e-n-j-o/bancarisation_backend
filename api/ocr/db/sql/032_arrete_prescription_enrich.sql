-- ============================================================================
-- Migration 032 — Enrichissement arrete_prescription (extracteur arrêté DAG)
-- Schéma : bancarisation
-- ----------------------------------------------------------------------------
-- Le script d'analyse d'arrêté (extract_arrete.py) produit un contrat plus
-- riche que la table 023 : code ART3-P2, opposabilité, phase, destinataire,
-- livrable, indicateur, obligation de résultat, temporalité jsonb.
-- Catégories ERC élargies (gouvernance / information / donnees).
--
-- Idempotent. À exécuter sur la base avant un run multidocs avec arrêté.
-- ============================================================================

ALTER TABLE bancarisation.arrete_prescription
  DROP CONSTRAINT IF EXISTS arrete_prescription_categorie_check;

ALTER TABLE bancarisation.arrete_prescription
  ADD CONSTRAINT arrete_prescription_categorie_check
  CHECK (
    categorie IS NULL OR categorie IN (
      'compensation',
      'evitement',
      'reduction',
      'accompagnement',
      'suivi',
      'chantier',
      'administratif',
      'gouvernance',
      'information',
      'donnees'
    )
  );

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS code text;

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS opposable boolean NOT NULL DEFAULT true;

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS phase text;

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS destinataire text;

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS livrable text;

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS indicateur text;

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS obligation_de_resultat boolean NOT NULL DEFAULT false;

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS temporalite jsonb;

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS autorite_destinataire jsonb;

ALTER TABLE bancarisation.arrete_prescription
  ADD COLUMN IF NOT EXISTS remarque text;

CREATE INDEX IF NOT EXISTS idx_presc_arrete_code
  ON bancarisation.arrete_prescription (arrete_id, code);

COMMENT ON COLUMN bancarisation.arrete_prescription.code IS
  'Identifiant court stable issu de l''extraction (ex. ART3-P2).';
COMMENT ON COLUMN bancarisation.arrete_prescription.opposable IS
  'False pour les articles procéduraux (recours, droits des tiers, publication).';
COMMENT ON COLUMN bancarisation.arrete_prescription.temporalite IS
  'Règle réglementaire extraite (délai relatif, récurrence, paliers, durée). '
  'Ne jamais y lire une date d''occurrence calendaire.';
