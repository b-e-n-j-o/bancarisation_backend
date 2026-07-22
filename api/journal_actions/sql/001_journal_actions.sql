-- 001_journal_actions.sql
-- Journal transversal des actions utilisateur (audit métier).
-- Même schéma que ocr/db/sql/015_journal_actions.sql (idempotent).

CREATE TABLE IF NOT EXISTS bancarisation.journal_actions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projet_id     uuid NULL REFERENCES bancarisation.projets(id) ON DELETE SET NULL,
  acteur        text NULL,                          -- identifiant / e-mail si dispo
  action        text NOT NULL,                       -- ex. bilan.supprimer, occurrence.modifier
  cible_type    text NULL,                          -- ex. rapport_bilan, occurrence
  cible_id      text NULL,
  detail        jsonb NOT NULL DEFAULT '{}'::jsonb,  -- contexte libre
  cree_le       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS journal_actions_projet_idx
  ON bancarisation.journal_actions (projet_id, cree_le DESC);

CREATE INDEX IF NOT EXISTS journal_actions_action_idx
  ON bancarisation.journal_actions (action, cree_le DESC);
