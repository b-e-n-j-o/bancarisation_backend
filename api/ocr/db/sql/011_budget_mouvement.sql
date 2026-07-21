-- 011_budget_mouvement.sql
-- Historique append-only des changements budgétaires, ligne par ligne.
-- Capté par trigger : toute modification d'un champ surveillé, quelle que
-- soit sa source (PATCH, figeage, import, SQL manuel), laisse une trace.
--
-- Le motif et l'auteur ne sont pas connus du trigger seul : l'appli les
-- passe via des réglages de session juste avant l'UPDATE, dans la MÊME
-- transaction :
--   SET LOCAL bancarisation.motif = 'report météo';
--   SET LOCAL bancarisation.modifie_par = 'benjamin';
-- (absents => NULL, ce qui est acceptable.)
--
-- Note : déjà appliqué en base chez certains environnements sous le nom
-- « 009_budget_mouvement » ; ce fichier aligne le dépôt (009 = prestataires).

CREATE TABLE IF NOT EXISTS bancarisation.budget_mouvement (
  id            uuid NOT NULL DEFAULT gen_random_uuid(),
  occurrence_id uuid NOT NULL,
  projet_id     uuid NOT NULL,
  champ         text NOT NULL,        -- montant_ht | montant_engage | montant_realise | statut | annee
  ancienne_val  text NULL,            -- texte : couvre montants ET statut/annee d'un seul schéma
  nouvelle_val  text NULL,
  motif         text NULL,
  modifie_par   text NULL,
  modifie_le    timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT budget_mouvement_pkey PRIMARY KEY (id),
  CONSTRAINT budget_mouvement_occurrence_fkey FOREIGN KEY (occurrence_id)
    REFERENCES bancarisation.occurrence (id) ON DELETE CASCADE,
  CONSTRAINT budget_mouvement_projet_fkey FOREIGN KEY (projet_id)
    REFERENCES bancarisation.projets (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS budget_mouvement_occurrence_idx
  ON bancarisation.budget_mouvement (occurrence_id, modifie_le DESC);
CREATE INDEX IF NOT EXISTS budget_mouvement_projet_idx
  ON bancarisation.budget_mouvement (projet_id, modifie_le DESC);

CREATE OR REPLACE FUNCTION bancarisation.log_budget_mouvement()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  v_motif text := current_setting('bancarisation.motif', true);
  v_par   text := current_setting('bancarisation.modifie_par', true);
BEGIN
  IF NEW.montant_ht IS DISTINCT FROM OLD.montant_ht THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'montant_ht',
            OLD.montant_ht::text, NEW.montant_ht::text, v_motif, v_par);
  END IF;

  IF NEW.montant_engage IS DISTINCT FROM OLD.montant_engage THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'montant_engage',
            OLD.montant_engage::text, NEW.montant_engage::text, v_motif, v_par);
  END IF;

  IF NEW.montant_realise IS DISTINCT FROM OLD.montant_realise THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'montant_realise',
            OLD.montant_realise::text, NEW.montant_realise::text, v_motif, v_par);
  END IF;

  IF NEW.statut IS DISTINCT FROM OLD.statut THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'statut',
            OLD.statut, NEW.statut, v_motif, v_par);
  END IF;

  IF NEW.annee IS DISTINCT FROM OLD.annee THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'annee',
            OLD.annee::text, NEW.annee::text, v_motif, v_par);
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_budget_mouvement ON bancarisation.occurrence;
CREATE TRIGGER trg_budget_mouvement
  AFTER UPDATE ON bancarisation.occurrence
  FOR EACH ROW
  EXECUTE FUNCTION bancarisation.log_budget_mouvement();
