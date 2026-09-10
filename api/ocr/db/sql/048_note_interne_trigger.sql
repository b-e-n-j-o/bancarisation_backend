-- =============================================================================
-- 048 — Trigger notes : lire l'occurrence en propriétaire
-- =============================================================================
-- tg_note_interne_before SELECT occurrence pour recopier projet_id.
-- Sans SECURITY DEFINER, la RLS occurrence masque la ligne (anon) →
-- projet_id devient NULL → INSERT refusé (NOT NULL).
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION bancarisation.tg_note_interne_before()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = bancarisation, pg_temp
AS $$
BEGIN
  IF NEW.occurrence_id IS NOT NULL THEN
    SELECT o.projet_id INTO NEW.projet_id
    FROM bancarisation.occurrence o
    WHERE o.id = NEW.occurrence_id;
    IF NEW.projet_id IS NULL THEN
      RAISE EXCEPTION 'Occurrence % introuvable — projet_id de la note impossible à résoudre.', NEW.occurrence_id;
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.contenu IS DISTINCT FROM OLD.contenu THEN
    NEW.modifie_le := now();
  END IF;
  RETURN NEW;
END $$;

GRANT EXECUTE ON FUNCTION bancarisation.tg_note_interne_before() TO anon, authenticated;

COMMIT;
