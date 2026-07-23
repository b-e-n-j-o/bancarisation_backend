-- 017_dialogue_v0.sql
-- Canal de dialogue DREAL ↔ bureau d'études — version démo.
--
-- Périmètre assumé V0 : pas de rôles, pas de notification, pas d'email.
-- L'acteur est déterminé par la section de l'app d'où l'on écrit
-- (« suivi DREAL » ou « projets »), et transmis par le front.
--
-- Colonnes conservées bien qu'inutilisées en V0, parce qu'elles ne coûtent
-- rien maintenant et obligeraient à requalifier l'historique plus tard :
--   * ancrage      : occurrence_id, annee, signal_code
--   * provenance   : origine ('application' | 'email')
--   * idempotence  : email_message_id

-- ═══════════════════════════════════════════════════════════════
-- 1. Demande — une par sujet, atomique
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS bancarisation.demande (
  id            uuid NOT NULL DEFAULT gen_random_uuid(),
  projet_id     uuid NOT NULL,
  -- Ancrage (tous nullables) : ce sur quoi porte la demande.
  occurrence_id uuid NULL,
  annee         integer NULL,        -- ex. bilan 2025 manquant
  signal_code   text NULL,           -- ex. 'bilan_manquant'
  objet         text NOT NULL,
  statut        text NOT NULL DEFAULT 'ouverte',
  auteur        text NOT NULL DEFAULT 'dreal',   -- côté à l'origine
  cree_le       timestamp with time zone NOT NULL DEFAULT now(),
  maj_le        timestamp with time zone NOT NULL DEFAULT now(),
  clos_le       timestamp with time zone NULL,
  CONSTRAINT demande_pkey PRIMARY KEY (id),
  CONSTRAINT demande_projet_fkey FOREIGN KEY (projet_id)
    REFERENCES bancarisation.projets (id) ON DELETE CASCADE,
  CONSTRAINT demande_occurrence_fkey FOREIGN KEY (occurrence_id)
    REFERENCES bancarisation.occurrence (id) ON DELETE SET NULL,
  CONSTRAINT demande_statut_chk CHECK (statut IN ('ouverte', 'repondue', 'close')),
  CONSTRAINT demande_auteur_chk CHECK (auteur IN ('dreal', 'be'))
);

CREATE INDEX IF NOT EXISTS demande_projet_idx
  ON bancarisation.demande (projet_id, cree_le DESC);
CREATE INDEX IF NOT EXISTS demande_statut_idx
  ON bancarisation.demande (statut) WHERE statut <> 'close';

-- ═══════════════════════════════════════════════════════════════
-- 2. Messages — append-only, comme budget_mouvement
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS bancarisation.demande_message (
  id               uuid NOT NULL DEFAULT gen_random_uuid(),
  demande_id       uuid NOT NULL,
  acteur           text NOT NULL,        -- 'dreal' | 'be'
  auteur_nom       text NULL,            -- saisi librement en V0
  corps            text NOT NULL,
  -- Pièces jointes : ids de la table documents existante (bucket Supabase).
  documents_ids    uuid[] NOT NULL DEFAULT '{}',
  origine          text NOT NULL DEFAULT 'application',
  email_message_id text NULL,
  cree_le          timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT demande_message_pkey PRIMARY KEY (id),
  CONSTRAINT demande_message_demande_fkey FOREIGN KEY (demande_id)
    REFERENCES bancarisation.demande (id) ON DELETE CASCADE,
  CONSTRAINT demande_message_acteur_chk CHECK (acteur IN ('dreal', 'be')),
  CONSTRAINT demande_message_origine_chk CHECK (origine IN ('application', 'email'))
);

CREATE INDEX IF NOT EXISTS demande_message_demande_idx
  ON bancarisation.demande_message (demande_id, cree_le ASC);

-- Idempotence future de l'ingestion email (un même mail relu deux fois
-- ne doit pas créer deux messages). Sans effet tant que la colonne est NULL.
CREATE UNIQUE INDEX IF NOT EXISTS demande_message_email_uniq
  ON bancarisation.demande_message (email_message_id)
  WHERE email_message_id IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════
-- 3. Statut de la demande tenu par trigger
-- ═══════════════════════════════════════════════════════════════
-- Un message du BE sur une demande ouverte la passe en 'repondue' ;
-- un message de la DREAL sur une demande répondue la rouvre (relance).
-- Une demande close ne change plus de statut automatiquement.

CREATE OR REPLACE FUNCTION bancarisation.maj_statut_demande()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  v_auteur text;
  v_statut text;
BEGIN
  SELECT auteur, statut INTO v_auteur, v_statut
  FROM bancarisation.demande WHERE id = NEW.demande_id;

  IF v_statut = 'close' THEN
    UPDATE bancarisation.demande SET maj_le = now() WHERE id = NEW.demande_id;
    RETURN NEW;
  END IF;

  UPDATE bancarisation.demande
  SET maj_le = now(),
      statut = CASE
        -- Réponse du camp opposé à celui qui a ouvert la demande
        WHEN NEW.acteur <> v_auteur THEN 'repondue'
        -- Relance par l'auteur : la demande redevient en attente
        ELSE 'ouverte'
      END
  WHERE id = NEW.demande_id;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_maj_statut_demande ON bancarisation.demande_message;
CREATE TRIGGER trg_maj_statut_demande
  AFTER INSERT ON bancarisation.demande_message
  FOR EACH ROW
  EXECUTE FUNCTION bancarisation.maj_statut_demande();

-- ═══════════════════════════════════════════════════════════════
-- 4. Fil conducteur du projet — DÉRIVÉ, jamais stocké
-- ═══════════════════════════════════════════════════════════════
-- Union des faits déjà en base. Aucun journal à maintenir en parallèle,
-- donc aucun risque de divergence avec la réalité.
-- Extensible : ajouter une CTE par type d'événement.

CREATE OR REPLACE VIEW bancarisation.v_projet_activite AS

WITH demandes AS (
  SELECT
    d.projet_id,
    d.cree_le            AS survenu_le,
    'demande_ouverte'    AS type,
    d.auteur             AS acteur,
    format('%s : %s',
           CASE d.auteur WHEN 'dreal' THEN 'Demande du service instructeur'
                         ELSE 'Signalement du bureau d''études' END,
           d.objet)      AS libelle,
    d.id                 AS demande_id
  FROM bancarisation.demande d
),
messages AS (
  SELECT
    d.projet_id,
    m.cree_le,
    'message',
    m.acteur,
    format('%s : %s',
           CASE m.acteur WHEN 'dreal' THEN 'Message du service instructeur'
                         ELSE 'Réponse du bureau d''études' END,
           left(m.corps, 120) || CASE WHEN length(m.corps) > 120 THEN '…' ELSE '' END),
    d.id
  FROM bancarisation.demande_message m
  JOIN bancarisation.demande d ON d.id = m.demande_id
),
clotures AS (
  SELECT
    d.projet_id, d.clos_le, 'demande_close', 'dreal',
    format('Demande close : %s', d.objet), d.id
  FROM bancarisation.demande d
  WHERE d.clos_le IS NOT NULL
),
bilans AS (
  SELECT
    rb.projet_id, rb.valide_le, 'bilan_valide', 'be',
    format('Bilan financier %s validé (version %s)', rb.annee, rb.version),
    NULL::uuid
  FROM bancarisation.rapport_bilan rb
  WHERE rb.statut = 'valide' AND rb.valide_le IS NOT NULL
),
baselines AS (
  SELECT
    bb.projet_id, bb.figee_le, 'baseline_figee', 'be',
    format('Budget de référence figé : %s', bb.libelle),
    NULL::uuid
  FROM bancarisation.budget_baseline bb
)
SELECT * FROM demandes
UNION ALL SELECT * FROM messages
UNION ALL SELECT * FROM clotures
UNION ALL SELECT * FROM bilans
UNION ALL SELECT * FROM baselines;