-- =============================================================================
-- Migration 027 — Module crédits biodiversité (Sites France Crédits Biodiversité)
-- =============================================================================
-- Additive uniquement. Aucune table existante n'est restructurée.
-- Ajouts sur l'existant :
--   - projets.type_dispositif           (NOT NULL DEFAULT 'obligation' -> non destructif)
--   - prescription_couverture.credit_vente_id (nullable, sans XOR — PK actuelle)
--
-- Aligné sur le schéma réel Kerelia :
--   - table `bancarisation.projets` (PLURIEL), PK `id uuid`
--   - `arrete.id` / `documents.id` / `constat_controle.id` = uuid
--   - échéance d'agrément = projets.date_fin (générée) : PAS de date_echeance
--     dupliquée dans site_agrement
--   - carbone / double comptage : hors périmètre
--   - occurrence (singulier) pour la trésorerie
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 0. Pivot : type de dispositif (axe ORTHOGONAL à type_procedure)
-- -----------------------------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE bancarisation.type_dispositif AS ENUM ('obligation', 'site_credits');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE bancarisation.projets
  ADD COLUMN IF NOT EXISTS type_dispositif bancarisation.type_dispositif
  NOT NULL DEFAULT 'obligation';

CREATE INDEX IF NOT EXISTS projets_credits_idx
  ON bancarisation.projets (organisation_id)
  WHERE type_dispositif = 'site_credits';


-- -----------------------------------------------------------------------------
-- 1. site_agrement — 1-1 avec un projet de type 'site_credits'
-- -----------------------------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE bancarisation.statut_agrement AS ENUM
    ('en_montage', 'agree', 'transfere', 'abroge');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS bancarisation.site_agrement (
  id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  projet_id            uuid NOT NULL UNIQUE
                         REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
  numero_agrement      text,
  prefet_region        text,
  arrete_id            uuid REFERENCES bancarisation.arrete(id),
  surface_totale_ha    numeric(12,4),
  surface_concernee_ha numeric(12,4),
  avis_instance        text,              -- 'CSRPN' | 'CNPN'
  statut               bancarisation.statut_agrement NOT NULL DEFAULT 'en_montage',
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);


-- -----------------------------------------------------------------------------
-- 2. credit_lot — l'émission de crédits (le "stock")
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bancarisation.credit_lot (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  site_agrement_id  bigint NOT NULL
                      REFERENCES bancarisation.site_agrement(id) ON DELETE CASCADE,
  libelle           text,
  composante_milieu text,
  methode_calcul    text,
  unite             text NOT NULL DEFAULT 'UCRR',
  quantite_emise    numeric(12,4) NOT NULL CHECK (quantite_emise > 0),
  date_emission     date,
  ug_codes          text[] NOT NULL DEFAULT '{}',
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_credit_lot_agrement
  ON bancarisation.credit_lot(site_agrement_id);


-- -----------------------------------------------------------------------------
-- 3. credit_vente — le registre des ventes
-- -----------------------------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE bancarisation.usage_credit AS ENUM
    ('compensation_reglementaire', 'compensation_volontaire', 'contribution_volontaire');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE bancarisation.statut_vente AS ENUM
    ('reserve', 'vendu', 'affecte', 'annule');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS bancarisation.credit_vente (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  lot_id            bigint NOT NULL
                      REFERENCES bancarisation.credit_lot(id) ON DELETE RESTRICT,
  quantite          numeric(12,4) NOT NULL CHECK (quantite > 0),
  usage_credit      bancarisation.usage_credit NOT NULL,
  acheteur_nom      text,
  acheteur_siren    text,
  arrete_acheteur   text,
  prix_unitaire_ht  numeric(12,2),
  prix_total_ht     numeric(12,2),
  date_reservation  date,
  date_vente        date,
  date_affectation  date,
  certificat_doc_id uuid REFERENCES bancarisation.documents(id) ON DELETE SET NULL,
  statut            bancarisation.statut_vente NOT NULL DEFAULT 'reserve',
  annee_encaissement int,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_credit_vente_lot
  ON bancarisation.credit_vente(lot_id);


-- -----------------------------------------------------------------------------
-- 4. Triggers d'intégrité
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION bancarisation.trg_credit_vente_stock()
RETURNS trigger AS $$
DECLARE
  v_emise  numeric(12,4);
  v_vendue numeric(12,4);
BEGIN
  SELECT quantite_emise INTO v_emise
    FROM bancarisation.credit_lot WHERE id = NEW.lot_id;

  SELECT COALESCE(SUM(quantite), 0) INTO v_vendue
    FROM bancarisation.credit_vente
    WHERE lot_id = NEW.lot_id
      AND statut <> 'annule'
      AND id <> COALESCE(NEW.id, -1);

  IF (v_vendue + NEW.quantite) > v_emise THEN
    RAISE EXCEPTION
      'Survente du lot % : emis=%, deja engage=%, tentative=%',
      NEW.lot_id, v_emise, v_vendue, NEW.quantite;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS credit_vente_stock ON bancarisation.credit_vente;
CREATE TRIGGER credit_vente_stock
  BEFORE INSERT OR UPDATE OF quantite, statut, lot_id
  ON bancarisation.credit_vente
  FOR EACH ROW EXECUTE FUNCTION bancarisation.trg_credit_vente_stock();


CREATE OR REPLACE FUNCTION bancarisation.trg_credit_vente_affecte_terminal()
RETURNS trigger AS $$
BEGIN
  IF OLD.statut = 'affecte' AND NEW.statut <> 'affecte' THEN
    RAISE EXCEPTION
      'Le statut affecte est terminal (vente %) : aucune reversion possible', OLD.id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS credit_vente_affecte_terminal ON bancarisation.credit_vente;
CREATE TRIGGER credit_vente_affecte_terminal
  BEFORE UPDATE OF statut ON bancarisation.credit_vente
  FOR EACH ROW EXECUTE FUNCTION bancarisation.trg_credit_vente_affecte_terminal();


-- -----------------------------------------------------------------------------
-- 5. constat_gain — suivi DÉCLARÉ (l'outil N'ÉVALUE PAS l'écologie)
-- -----------------------------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE bancarisation.statut_constat AS ENUM
    ('atteint', 'partiel', 'non_atteint', 'non_evalue');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE bancarisation.source_constat AS ENUM
    ('inventaire', 'constat_controle', 'satellite');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS bancarisation.constat_gain (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  lot_id              bigint NOT NULL
                        REFERENCES bancarisation.credit_lot(id) ON DELETE CASCADE,
  annee_palier        int,
  libelle_palier      text,
  date_campagne       date NOT NULL,
  statut              bancarisation.statut_constat NOT NULL DEFAULT 'non_evalue',
  source              bancarisation.source_constat NOT NULL DEFAULT 'inventaire',
  constat_controle_id uuid REFERENCES bancarisation.constat_controle(id) ON DELETE SET NULL,
  rapport_doc_id      uuid REFERENCES bancarisation.documents(id) ON DELETE SET NULL,
  commentaire         text,
  created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_constat_gain_lot
  ON bancarisation.constat_gain(lot_id);


-- -----------------------------------------------------------------------------
-- 6. Raccord acheteur — couverture de prescription par crédit acquis
-- -----------------------------------------------------------------------------
-- Note : echeance_id est NOT NULL + partie de la PK composite actuelle.
-- On ajoute seulement la FK nullable ; le XOR echeance/crédit exigera une
-- migration dédiée (assouplir la PK) — hors démo.
ALTER TABLE bancarisation.prescription_couverture
  ADD COLUMN IF NOT EXISTS credit_vente_id bigint
    REFERENCES bancarisation.credit_vente(id) ON DELETE SET NULL;


-- -----------------------------------------------------------------------------
-- 7. Vues métier (interface de lecture du front)
-- -----------------------------------------------------------------------------

-- 7a. Stock par lot
CREATE OR REPLACE VIEW bancarisation.v_credit_stock AS
SELECT
  l.id                                       AS lot_id,
  l.site_agrement_id,
  sa.projet_id,
  l.libelle,
  l.composante_milieu,
  l.unite,
  l.date_emission,
  l.quantite_emise,
  COALESCE(SUM(v.quantite) FILTER (WHERE v.statut = 'reserve'), 0)  AS qte_reservee,
  COALESCE(SUM(v.quantite) FILTER (WHERE v.statut = 'vendu'),   0)  AS qte_vendue,
  COALESCE(SUM(v.quantite) FILTER (WHERE v.statut = 'affecte'), 0)  AS qte_affectee,
  l.quantite_emise
    - COALESCE(SUM(v.quantite) FILTER (WHERE v.statut <> 'annule'), 0) AS qte_disponible
FROM bancarisation.credit_lot l
JOIN bancarisation.site_agrement sa ON sa.id = l.site_agrement_id
LEFT JOIN bancarisation.credit_vente v ON v.lot_id = l.id
GROUP BY l.id, sa.projet_id;

-- 7b. Suivi du gain : NE JUGE RIEN. Signale les paliers échus sans constat.
CREATE OR REPLACE VIEW bancarisation.v_credit_suivi AS
SELECT
  l.id                          AS lot_id,
  l.site_agrement_id,
  sa.projet_id,
  l.libelle,
  l.quantite_emise,
  s.qte_vendue + s.qte_affectee AS qte_engagee,
  cg.annee_palier,
  cg.libelle_palier,
  MAX(cg.date_campagne)         AS dernier_constat,
  (
    cg.annee_palier IS NOT NULL
    AND cg.annee_palier <= EXTRACT(YEAR FROM now())
    AND COUNT(cg.id) FILTER (WHERE cg.date_campagne IS NOT NULL) = 0
  )                             AS manque_constat
FROM bancarisation.credit_lot l
JOIN bancarisation.site_agrement sa ON sa.id = l.site_agrement_id
JOIN bancarisation.v_credit_stock s ON s.lot_id = l.id
LEFT JOIN bancarisation.constat_gain cg ON cg.lot_id = l.id
GROUP BY l.id, sa.projet_id, s.qte_vendue, s.qte_affectee, cg.annee_palier, cg.libelle_palier;

-- 7c. Registre des ventes (bloc 1 du rapport annuel)
CREATE OR REPLACE VIEW bancarisation.v_registre_ventes AS
SELECT
  sa.projet_id,
  sa.numero_agrement,
  l.id                AS lot_id,
  l.libelle           AS lot_libelle,
  l.composante_milieu,
  v.id                AS vente_id,
  v.quantite,
  v.usage_credit,
  v.acheteur_nom,
  v.acheteur_siren,
  v.arrete_acheteur,
  v.prix_unitaire_ht,
  v.prix_total_ht,
  v.date_vente,
  v.date_affectation,
  v.date_reservation,
  v.statut,
  v.annee_encaissement,
  v.certificat_doc_id
FROM bancarisation.credit_vente v
JOIN bancarisation.credit_lot l      ON l.id = v.lot_id
JOIN bancarisation.site_agrement sa  ON sa.id = l.site_agrement_id
WHERE v.statut <> 'annule';

-- 7d. Trésorerie site (dépenses occurrences + recettes ventes)
CREATE OR REPLACE VIEW bancarisation.v_tresorerie_site AS
WITH dep AS (
  SELECT
    o.projet_id,
    o.annee,
    SUM(COALESCE(o.montant_ht, 0)) AS depenses
  FROM bancarisation.occurrence o
  WHERE o.statut <> 'supprime'
  GROUP BY o.projet_id, o.annee
),
rec AS (
  SELECT
    sa.projet_id,
    v.annee_encaissement AS annee,
    SUM(COALESCE(v.prix_total_ht, 0)) AS recettes
  FROM bancarisation.credit_vente v
  JOIN bancarisation.credit_lot l     ON l.id = v.lot_id
  JOIN bancarisation.site_agrement sa ON sa.id = l.site_agrement_id
  WHERE v.statut <> 'annule'
    AND v.annee_encaissement IS NOT NULL
  GROUP BY sa.projet_id, v.annee_encaissement
)
SELECT
  COALESCE(d.projet_id, r.projet_id) AS projet_id,
  COALESCE(d.annee, r.annee)         AS annee,
  COALESCE(d.depenses, 0)            AS depenses,
  COALESCE(r.recettes, 0)            AS recettes,
  COALESCE(r.recettes, 0) - COALESCE(d.depenses, 0) AS solde
FROM dep d
FULL OUTER JOIN rec r
  ON d.projet_id = r.projet_id AND d.annee = r.annee;

COMMIT;

-- =============================================================================
-- Rollback :
-- BEGIN;
-- DROP VIEW IF EXISTS bancarisation.v_tresorerie_site;
-- DROP VIEW IF EXISTS bancarisation.v_registre_ventes;
-- DROP VIEW IF EXISTS bancarisation.v_credit_suivi;
-- DROP VIEW IF EXISTS bancarisation.v_credit_stock;
-- ALTER TABLE bancarisation.prescription_couverture
--   DROP COLUMN IF EXISTS credit_vente_id;
-- DROP TABLE IF EXISTS bancarisation.constat_gain;
-- DROP TABLE IF EXISTS bancarisation.credit_vente;
-- DROP TABLE IF EXISTS bancarisation.credit_lot;
-- DROP TABLE IF EXISTS bancarisation.site_agrement;
-- DROP INDEX IF EXISTS bancarisation.projets_credits_idx;
-- ALTER TABLE bancarisation.projets DROP COLUMN IF EXISTS type_dispositif;
-- DROP TYPE IF EXISTS bancarisation.source_constat;
-- DROP TYPE IF EXISTS bancarisation.statut_constat;
-- DROP TYPE IF EXISTS bancarisation.statut_vente;
-- DROP TYPE IF EXISTS bancarisation.usage_credit;
-- DROP TYPE IF EXISTS bancarisation.statut_agrement;
-- DROP TYPE IF EXISTS bancarisation.type_dispositif;
-- COMMIT;
-- =============================================================================
