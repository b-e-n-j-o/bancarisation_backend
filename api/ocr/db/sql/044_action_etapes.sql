-- =============================================================================
-- Migration 044 — Checklist / étapes d'une action
-- =============================================================================
-- Tables modèles + instances, RPC d'application, agrégats en fin de
-- v_action_portefeuille. Le statut d'occurrence n'est jamais muté ici.
-- FK occurrence_id : NO ACTION (un ré-import ne doit pas tout effacer).
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Modèles (portée organisation, V0 ouverte comme personnes)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bancarisation.checklist_modele (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid REFERENCES bancarisation.organisations(id) ON DELETE SET NULL,
  nom             text NOT NULL,
  description     text,
  archive         boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bancarisation.checklist_modele_etape (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  modele_id   uuid NOT NULL REFERENCES bancarisation.checklist_modele(id) ON DELETE CASCADE,
  ordre       int  NOT NULL,
  libelle     text NOT NULL,
  description text,
  role_code   text
);

CREATE INDEX IF NOT EXISTS checklist_modele_etape_idx
  ON bancarisation.checklist_modele_etape (modele_id, ordre);

-- ---------------------------------------------------------------------------
-- 2. Étapes d'une action
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bancarisation.action_etape (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  occurrence_id   uuid NOT NULL REFERENCES bancarisation.occurrence(id), -- NO ACTION
  projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
  ordre           int  NOT NULL,
  libelle         text NOT NULL CHECK (length(trim(libelle)) > 0),
  description     text,
  assigne_a       uuid REFERENCES bancarisation.personnes(id) ON DELETE SET NULL,
  echeance        date,
  fait            boolean NOT NULL DEFAULT false,
  fait_le         timestamptz,
  fait_par        uuid REFERENCES bancarisation.personnes(id) ON DELETE SET NULL,
  modele_etape_id uuid,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS action_etape_occ_idx
  ON bancarisation.action_etape (occurrence_id, ordre);
CREATE INDEX IF NOT EXISTS action_etape_projet_idx
  ON bancarisation.action_etape (projet_id);
CREATE INDEX IF NOT EXISTS action_etape_fait_idx
  ON bancarisation.action_etape (projet_id, fait_le DESC)
  WHERE fait;

COMMENT ON TABLE bancarisation.action_etape IS
  'Checklist d''une action. Ne mute jamais occurrence.statut.';
COMMENT ON COLUMN bancarisation.action_etape.occurrence_id IS
  'FK NO ACTION : un ré-import ne doit pas supprimer silencieusement les étapes.';

-- ---------------------------------------------------------------------------
-- 3. Trigger projet_id + fait_le / fait_par
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION bancarisation.tg_action_etape_before()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.projet_id IS NULL THEN
    SELECT o.projet_id INTO NEW.projet_id
    FROM bancarisation.occurrence o WHERE o.id = NEW.occurrence_id;
  END IF;

  IF TG_OP = 'INSERT' THEN
    IF NEW.fait THEN NEW.fait_le := coalesce(NEW.fait_le, now()); END IF;
  ELSIF NEW.fait AND NOT OLD.fait THEN
    NEW.fait_le := coalesce(NEW.fait_le, now());
  ELSIF NOT NEW.fait AND OLD.fait THEN
    NEW.fait_le  := NULL;
    NEW.fait_par := NULL;
  END IF;

  NEW.updated_at := now();
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS action_etape_before ON bancarisation.action_etape;
CREATE TRIGGER action_etape_before
  BEFORE INSERT OR UPDATE ON bancarisation.action_etape
  FOR EACH ROW EXECUTE FUNCTION bancarisation.tg_action_etape_before();

-- ---------------------------------------------------------------------------
-- 4. RPC — SECURITY INVOKER : la RLS s'applique aux INSERT
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION bancarisation.appliquer_checklist_modele(
  p_modele_id uuid, p_occurrence_ids uuid[])
RETURNS integer LANGUAGE sql AS $$
  WITH base AS (
    SELECT o.id AS occurrence_id, coalesce(max(e.ordre), 0) AS ordre_max
    FROM bancarisation.occurrence o
    LEFT JOIN bancarisation.action_etape e ON e.occurrence_id = o.id
    WHERE o.id = ANY(p_occurrence_ids)
    GROUP BY o.id
  ), ins AS (
    INSERT INTO bancarisation.action_etape
      (occurrence_id, ordre, libelle, description, modele_etape_id)
    SELECT b.occurrence_id, b.ordre_max + me.ordre, me.libelle, me.description, me.id
    FROM base b
    CROSS JOIN bancarisation.checklist_modele_etape me
    WHERE me.modele_id = p_modele_id
      AND NOT EXISTS (
        SELECT 1 FROM bancarisation.action_etape x
        WHERE x.occurrence_id = b.occurrence_id
          AND x.modele_etape_id = me.id
      )
    RETURNING 1
  )
  SELECT count(*)::int FROM ins;
$$;

CREATE OR REPLACE FUNCTION bancarisation.creer_modele_depuis_action(
  p_occurrence_id uuid, p_nom text, p_organisation_id uuid)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO bancarisation.checklist_modele (organisation_id, nom)
  VALUES (p_organisation_id, p_nom) RETURNING id INTO v_id;

  INSERT INTO bancarisation.checklist_modele_etape (modele_id, ordre, libelle, description)
  SELECT v_id, (row_number() OVER (ORDER BY ordre))::int, libelle, description
  FROM bancarisation.action_etape
  WHERE occurrence_id = p_occurrence_id;

  RETURN v_id;
END $$;

-- ---------------------------------------------------------------------------
-- 5. RLS — même filet V0 que projet_contact (anon sans JWT)
-- ---------------------------------------------------------------------------

ALTER TABLE bancarisation.action_etape ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS action_etape_lecture ON bancarisation.action_etape;
CREATE POLICY action_etape_lecture ON bancarisation.action_etape
  FOR SELECT
  USING (
    bancarisation.peut_lire_projet(projet_id)
    OR auth.uid() IS NULL
  );
DROP POLICY IF EXISTS action_etape_ecriture ON bancarisation.action_etape;
CREATE POLICY action_etape_ecriture ON bancarisation.action_etape
  FOR ALL
  USING (
    bancarisation.peut_ecrire_projet(projet_id)
    OR auth.uid() IS NULL
  )
  WITH CHECK (
    bancarisation.peut_ecrire_projet(projet_id)
    OR auth.uid() IS NULL
  );

ALTER TABLE bancarisation.checklist_modele ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS v0_open ON bancarisation.checklist_modele;
CREATE POLICY v0_open ON bancarisation.checklist_modele
  FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE bancarisation.checklist_modele_etape ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS v0_open ON bancarisation.checklist_modele_etape;
CREATE POLICY v0_open ON bancarisation.checklist_modele_etape
  FOR ALL USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- 6. Agrégats étapes en FIN de v_action_portefeuille (pas de notes : lot 4)
-- ---------------------------------------------------------------------------

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
  et.etape_courante
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
) et ON true;

-- ---------------------------------------------------------------------------
-- 7. Seed modèles démo (idempotent)
-- ---------------------------------------------------------------------------

INSERT INTO bancarisation.checklist_modele (nom, description)
SELECT 'Travaux terrain', 'Préparation, intervention, restitution'
WHERE NOT EXISTS (
  SELECT 1 FROM bancarisation.checklist_modele WHERE nom = 'Travaux terrain'
);

INSERT INTO bancarisation.checklist_modele (nom, description)
SELECT 'Suivi écologique', 'Protocole, terrain, saisie, rapport'
WHERE NOT EXISTS (
  SELECT 1 FROM bancarisation.checklist_modele WHERE nom = 'Suivi écologique'
);

INSERT INTO bancarisation.checklist_modele_etape (modele_id, ordre, libelle)
SELECT m.id, x.ordre, x.libelle
FROM bancarisation.checklist_modele m
JOIN (VALUES
  (1, 'Préparer le chantier'),
  (2, 'Intervenir sur site'),
  (3, 'Rédiger le compte-rendu'),
  (4, 'Déposer les photos')
) AS x(ordre, libelle) ON true
WHERE m.nom = 'Travaux terrain'
  AND NOT EXISTS (
    SELECT 1 FROM bancarisation.checklist_modele_etape e WHERE e.modele_id = m.id
  );

INSERT INTO bancarisation.checklist_modele_etape (modele_id, ordre, libelle)
SELECT m.id, x.ordre, x.libelle
FROM bancarisation.checklist_modele m
JOIN (VALUES
  (1, 'Préparer le protocole'),
  (2, 'Passage terrain'),
  (3, 'Saisir les observations'),
  (4, 'Rédiger le rapport de suivi')
) AS x(ordre, libelle) ON true
WHERE m.nom = 'Suivi écologique'
  AND NOT EXISTS (
    SELECT 1 FROM bancarisation.checklist_modele_etape e WHERE e.modele_id = m.id
  );

-- ---------------------------------------------------------------------------
-- 8. Grants
-- ---------------------------------------------------------------------------

GRANT SELECT, INSERT, UPDATE, DELETE ON
  bancarisation.checklist_modele,
  bancarisation.checklist_modele_etape,
  bancarisation.action_etape
  TO anon, authenticated;

GRANT SELECT ON bancarisation.v_action_portefeuille TO anon, authenticated;

GRANT EXECUTE ON FUNCTION
  bancarisation.appliquer_checklist_modele(uuid, uuid[]),
  bancarisation.creer_modele_depuis_action(uuid, text, uuid)
  TO anon, authenticated;

COMMIT;

NOTIFY pgrst, 'reload schema';
