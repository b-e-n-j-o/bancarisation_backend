-- ============================================================================
-- Migration 026 — Rapport de suivi écologique (snapshot versionné)
-- Schéma : bancarisation
-- ----------------------------------------------------------------------------
-- Distinct de rapport_bilan (financier) et enveloppe workflow bilan_suivi (023).
-- Cycle BE : génération snapshot → PDF → dépôt → bilan_suivi.statut = 'depose'
-- (bannette DREAL). Une version figée est immuable ; correction = nouvelle version.
-- ============================================================================

CREATE TABLE IF NOT EXISTS bancarisation.rapport_suivi (
  id            uuid NOT NULL DEFAULT gen_random_uuid(),
  projet_id     uuid NOT NULL,
  annee         integer NOT NULL,
  version       integer NOT NULL DEFAULT 1,
  statut        text NOT NULL DEFAULT 'genere'
                  CHECK (statut IN ('genere', 'depose')),
  genere_le     timestamptz NOT NULL DEFAULT now(),
  genere_par    text NULL,
  depose_le     timestamptz NULL,
  borne_donnees timestamptz NULL,
  donnees       jsonb NOT NULL DEFAULT '{}'::jsonb,
  controles     jsonb NOT NULL DEFAULT '[]'::jsonb,
  document_id   uuid REFERENCES bancarisation.documents(id) ON DELETE SET NULL,
  bilan_suivi_id uuid REFERENCES bancarisation.bilan_suivi(id) ON DELETE SET NULL,
  CONSTRAINT rapport_suivi_pkey PRIMARY KEY (id),
  CONSTRAINT rapport_suivi_projet_fkey FOREIGN KEY (projet_id)
    REFERENCES bancarisation.projets (id) ON DELETE CASCADE,
  CONSTRAINT rapport_suivi_unique UNIQUE (projet_id, annee, version)
);

CREATE INDEX IF NOT EXISTS rapport_suivi_projet_annee_idx
  ON bancarisation.rapport_suivi (projet_id, annee, version DESC);

CREATE INDEX IF NOT EXISTS rapport_suivi_bilan_idx
  ON bancarisation.rapport_suivi (bilan_suivi_id)
  WHERE bilan_suivi_id IS NOT NULL;

COMMENT ON TABLE bancarisation.rapport_suivi IS
  'Snapshot versionné du bilan de suivi écologique (occurrences + preuves + commentaires BE).';

-- Lien inverse optionnel sur l'enveloppe d'instruction
ALTER TABLE bancarisation.bilan_suivi
  ADD COLUMN IF NOT EXISTS rapport_suivi_id uuid
    REFERENCES bancarisation.rapport_suivi(id) ON DELETE SET NULL;
