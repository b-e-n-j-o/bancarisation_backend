-- 013_rapport_bilan.sql
-- Archivage des bilans financiers annuels.
--
-- Principes d'auditabilité :
--  * Un bilan est un SNAPSHOT : `donnees` (jsonb) contient tous les chiffres
--    au moment de la génération. On ne recalcule jamais un bilan archivé.
--  * Cycle : brouillon -> valide. Une fois validé, un bilan est IMMUABLE
--    (trigger de protection ci-dessous) ; toute correction = nouvelle version.
--  * unique (projet, annee, version) : la série des versions est traçable.
--  * `borne_donnees` : horodatage du dernier mouvement inclus — permet de
--    vérifier plus tard la fidélité du bilan aux données de son époque.

CREATE TABLE IF NOT EXISTS bancarisation.rapport_bilan (
  id            uuid NOT NULL DEFAULT gen_random_uuid(),
  projet_id     uuid NOT NULL,
  annee         integer NOT NULL,
  version       integer NOT NULL DEFAULT 1,
  statut        text NOT NULL DEFAULT 'brouillon',   -- brouillon | valide
  genere_le     timestamp with time zone NOT NULL DEFAULT now(),
  genere_par    text NULL,
  valide_le     timestamp with time zone NULL,
  valide_par    text NULL,
  borne_donnees timestamp with time zone NULL,
  baseline_id   uuid NULL,
  donnees       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- snapshot complet du bilan
  controles     jsonb NOT NULL DEFAULT '[]'::jsonb,  -- points relevés à la génération
  document_id   uuid NULL,                           -- PDF rattaché (plus tard)
  CONSTRAINT rapport_bilan_pkey PRIMARY KEY (id),
  CONSTRAINT rapport_bilan_projet_fkey FOREIGN KEY (projet_id)
    REFERENCES bancarisation.projets (id) ON DELETE CASCADE,
  CONSTRAINT rapport_bilan_baseline_fkey FOREIGN KEY (baseline_id)
    REFERENCES bancarisation.budget_baseline (id) ON DELETE SET NULL,
  CONSTRAINT rapport_bilan_statut_chk CHECK (statut IN ('brouillon', 'valide')),
  CONSTRAINT rapport_bilan_unique UNIQUE (projet_id, annee, version)
);

CREATE INDEX IF NOT EXISTS rapport_bilan_projet_annee_idx
  ON bancarisation.rapport_bilan (projet_id, annee, version DESC);

-- ---------------------------------------------------------------
-- Protection d'immuabilité : un bilan validé ne peut plus changer,
-- à l'exception du rattachement du PDF (document_id), qui peut être
-- posé après validation.
-- ---------------------------------------------------------------

CREATE OR REPLACE FUNCTION bancarisation.proteger_rapport_valide()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.statut = 'valide' THEN
    IF NEW.id            IS DISTINCT FROM OLD.id
       OR NEW.projet_id  IS DISTINCT FROM OLD.projet_id
       OR NEW.annee      IS DISTINCT FROM OLD.annee
       OR NEW.version    IS DISTINCT FROM OLD.version
       OR NEW.statut     IS DISTINCT FROM OLD.statut
       OR NEW.genere_le  IS DISTINCT FROM OLD.genere_le
       OR NEW.genere_par IS DISTINCT FROM OLD.genere_par
       OR NEW.valide_le  IS DISTINCT FROM OLD.valide_le
       OR NEW.valide_par IS DISTINCT FROM OLD.valide_par
       OR NEW.borne_donnees IS DISTINCT FROM OLD.borne_donnees
       OR NEW.baseline_id   IS DISTINCT FROM OLD.baseline_id
       OR NEW.donnees    IS DISTINCT FROM OLD.donnees
       OR NEW.controles  IS DISTINCT FROM OLD.controles
    THEN
      RAISE EXCEPTION 'Bilan validé : immuable (seul document_id peut être rattaché). Générer une nouvelle version.';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_proteger_rapport_valide ON bancarisation.rapport_bilan;
CREATE TRIGGER trg_proteger_rapport_valide
  BEFORE UPDATE ON bancarisation.rapport_bilan
  FOR EACH ROW
  EXECUTE FUNCTION bancarisation.proteger_rapport_valide();

-- Suppression : on interdit aussi la suppression d'un bilan validé.
CREATE OR REPLACE FUNCTION bancarisation.proteger_rapport_valide_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.statut = 'valide' THEN
    RAISE EXCEPTION 'Bilan validé : suppression interdite.';
  END IF;
  RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_proteger_rapport_valide_del ON bancarisation.rapport_bilan;
CREATE TRIGGER trg_proteger_rapport_valide_del
  BEFORE DELETE ON bancarisation.rapport_bilan
  FOR EACH ROW
  EXECUTE FUNCTION bancarisation.proteger_rapport_valide_delete();