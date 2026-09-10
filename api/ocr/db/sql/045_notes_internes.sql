-- =============================================================================
-- Migration 045 — Notes internes + fil
-- =============================================================================
-- Distinct du canal DREAL (demande / DialogueView) : ne pas toucher.
-- Suppression logique (supprime_le). FK occurrence_id : NO ACTION.
-- RLS peut_lire_interne : masque le rôle controleur (JWT). Filet V0 anon.
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION bancarisation.peut_lire_interne(p_projet_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = bancarisation, pg_temp
AS $$
  SELECT
    bancarisation.peut_lire_projet(p_projet_id)
    AND coalesce(bancarisation.role_courant(), '') <> 'controleur'
$$;

CREATE TABLE IF NOT EXISTS bancarisation.note_interne (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projet_id     uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
  occurrence_id uuid REFERENCES bancarisation.occurrence(id), -- NO ACTION
  auteur_id     uuid REFERENCES bancarisation.personnes(id) ON DELETE SET NULL,
  contenu       text NOT NULL CHECK (length(trim(contenu)) > 0),
  mentions      uuid[] NOT NULL DEFAULT '{}',
  document_id   uuid REFERENCES bancarisation.documents(id) ON DELETE SET NULL,
  etape_id      uuid REFERENCES bancarisation.action_etape(id) ON DELETE SET NULL,
  epinglee      boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  modifie_le    timestamptz,
  supprime_le   timestamptz
);

CREATE INDEX IF NOT EXISTS note_interne_projet_idx
  ON bancarisation.note_interne (projet_id, created_at DESC);
CREATE INDEX IF NOT EXISTS note_interne_occ_idx
  ON bancarisation.note_interne (occurrence_id, created_at)
  WHERE occurrence_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS note_interne_mentions_idx
  ON bancarisation.note_interne USING gin (mentions);

COMMENT ON TABLE bancarisation.note_interne IS
  'Discussion interne. Jamais le canal DREAL. Invisible au rôle controleur.';
COMMENT ON COLUMN bancarisation.note_interne.occurrence_id IS
  'NULL = note de projet. FK NO ACTION (ré-import).';

CREATE OR REPLACE FUNCTION bancarisation.tg_note_interne_before()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = bancarisation, pg_temp
AS $$
BEGIN
  IF NEW.occurrence_id IS NOT NULL THEN
    SELECT o.projet_id INTO NEW.projet_id
    FROM bancarisation.occurrence o WHERE o.id = NEW.occurrence_id;
    IF NEW.projet_id IS NULL THEN
      RAISE EXCEPTION 'Occurrence % introuvable — projet_id de la note impossible à résoudre.', NEW.occurrence_id;
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.contenu IS DISTINCT FROM OLD.contenu THEN
    NEW.modifie_le := now();
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS note_interne_before ON bancarisation.note_interne;
CREATE TRIGGER note_interne_before
  BEFORE INSERT OR UPDATE ON bancarisation.note_interne
  FOR EACH ROW EXECUTE FUNCTION bancarisation.tg_note_interne_before();

ALTER TABLE bancarisation.note_interne ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS note_interne_lecture ON bancarisation.note_interne;
CREATE POLICY note_interne_lecture ON bancarisation.note_interne
  FOR SELECT
  USING (
    bancarisation.peut_lire_interne(projet_id)
    OR auth.uid() IS NULL
  );

DROP POLICY IF EXISTS note_interne_ecriture ON bancarisation.note_interne;
CREATE POLICY note_interne_ecriture ON bancarisation.note_interne
  FOR ALL
  USING (
    bancarisation.peut_ecrire_projet(projet_id)
    OR auth.uid() IS NULL
  )
  WITH CHECK (
    bancarisation.peut_ecrire_projet(projet_id)
    OR auth.uid() IS NULL
  );

-- Fil : notes + étapes cochées (pas de branche budget_mouvement)
CREATE OR REPLACE VIEW bancarisation.v_fil_interne
WITH (security_invoker = true) AS
WITH evt AS (
  SELECT
    'note'::text AS type,
    n.id,
    n.projet_id,
    n.occurrence_id,
    n.auteur_id AS personne_id,
    n.contenu AS texte,
    n.created_at AS horodatage,
    n.document_id,
    n.mentions,
    n.modifie_le,
    n.epinglee
  FROM bancarisation.note_interne n
  WHERE n.supprime_le IS NULL
  UNION ALL
  SELECT
    'etape'::text,
    e.id,
    e.projet_id,
    e.occurrence_id,
    e.fait_par,
    e.libelle,
    e.fait_le,
    NULL::uuid,
    '{}'::uuid[],
    NULL::timestamptz,
    false
  FROM bancarisation.action_etape e
  WHERE e.fait AND e.fait_le IS NOT NULL
)
SELECT
  evt.*,
  p.nom AS projet_nom,
  p.reference_interne AS projet_reference,
  p.organisation_id,
  o.code AS action_code,
  o.titre AS action_titre,
  o.annee AS action_annee,
  COALESCE(NULLIF(trim(concat_ws(' ', pe.prenom, pe.nom)), ''), pe.nom) AS personne_nom
FROM evt
JOIN bancarisation.projets p ON p.id = evt.projet_id
LEFT JOIN bancarisation.occurrence o ON o.id = evt.occurrence_id
LEFT JOIN bancarisation.personnes pe ON pe.id = evt.personne_id;

-- Agrégats notes en FIN de v_action_portefeuille
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
   AND fen.date_fin_fenetre < current_date) AS en_retard,
  et.nb_etapes,
  et.nb_etapes_faites,
  et.etape_courante,
  nt.nb_notes,
  nt.derniere_note_le
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
) fen
LEFT JOIN LATERAL (
  SELECT
    count(*)::int AS nb_etapes,
    count(*) FILTER (WHERE e.fait)::int AS nb_etapes_faites,
    (SELECT e2.libelle FROM bancarisation.action_etape e2
      WHERE e2.occurrence_id = vc.id AND NOT e2.fait
      ORDER BY e2.ordre LIMIT 1) AS etape_courante
  FROM bancarisation.action_etape e
  WHERE e.occurrence_id = vc.id
) et ON true
LEFT JOIN LATERAL (
  SELECT
    count(*)::int AS nb_notes,
    max(n.created_at) AS derniere_note_le
  FROM bancarisation.note_interne n
  WHERE n.occurrence_id = vc.id AND n.supprime_le IS NULL
) nt ON true;

GRANT SELECT, INSERT, UPDATE, DELETE ON bancarisation.note_interne TO anon, authenticated;
GRANT SELECT ON bancarisation.v_fil_interne TO anon, authenticated;
GRANT SELECT ON bancarisation.v_action_portefeuille TO anon, authenticated;
GRANT EXECUTE ON FUNCTION bancarisation.peut_lire_interne(uuid) TO anon, authenticated;

COMMIT;

NOTIFY pgrst, 'reload schema';
