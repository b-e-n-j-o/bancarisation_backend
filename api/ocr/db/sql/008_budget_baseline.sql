-- 008_budget_baseline.sql
-- Baseline budgétaire + trois états financiers.
--
-- Modèle :
--   montant_ht       = PRÉVU   (budget vivant, éditable — colonne existante)
--   montant_engage   = ENGAGÉ  (devis signé / bon de commande)
--   montant_realise  = RÉALISÉ (facturé)
--   montant_initial + annee_initiale = BASELINE, figée à la validation,
--     jamais modifiée par le user ensuite. annee_initiale est indispensable :
--     sans elle, une occurrence repoussée perd la trace de son année d'origine
--     et on ne peut plus calculer les montants "glissés".
--
-- Lecture des deltas :
--   montant_initial IS NULL            → occurrence AJOUTÉE après baseline
--   statut = 'supprime' + initial ≠ ø  → occurrence ANNULÉE (économie)
--   annee ≠ annee_initiale             → montant GLISSÉ (report)
--   annee = annee_initiale, montants ≠ → RÉVISION de prix

-- ---------------------------------------------------------------
-- 1. Colonnes occurrence
-- ---------------------------------------------------------------

ALTER TABLE bancarisation.occurrence
  ADD COLUMN IF NOT EXISTS montant_initial numeric NULL,
  ADD COLUMN IF NOT EXISTS annee_initiale integer NULL,
  ADD COLUMN IF NOT EXISTS montant_engage numeric NULL,
  ADD COLUMN IF NOT EXISTS montant_realise numeric NULL;

-- ---------------------------------------------------------------
-- 2. Historique des figeages de baseline
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bancarisation.budget_baseline (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  projet_id uuid NOT NULL,
  figee_le timestamp with time zone NOT NULL DEFAULT now(),
  libelle text NOT NULL DEFAULT 'Baseline',
  commentaire text NULL,
  mode text NOT NULL DEFAULT 'completer',
  nb_occurrences integer NOT NULL,
  total_ht numeric NOT NULL,
  CONSTRAINT budget_baseline_pkey PRIMARY KEY (id),
  CONSTRAINT budget_baseline_projet_fkey FOREIGN KEY (projet_id)
    REFERENCES bancarisation.projets (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS budget_baseline_projet_idx
  ON bancarisation.budget_baseline (projet_id, figee_le DESC);

-- ---------------------------------------------------------------
-- 3. v_occurrence_calendrier : nouvelles colonnes AJOUTÉES EN FIN
-- ---------------------------------------------------------------

CREATE OR REPLACE VIEW bancarisation.v_occurrence_calendrier AS
 SELECT o.id,
    o.projet_id,
    o.echeance_id,
    o.annee,
    o.code,
    o.titre,
    o.categorie,
    o.statut,
    o.ug_ids,
    o.mois_debut,
    o.mois_fin,
    o.traverse_nouvel_an,
    o.origine,
    o.confiance,
    o.champs_a_confirmer,
    o.avertissements,
    o.modifie_le,
    o.date_realisation,
    o.commentaire,
    e.cle AS echeance_cle,
    e.code_operation,
    e.libelle AS echeance_libelle,
    e.action_cle,
    COALESCE(NULLIF(NULLIF(o.lib_thema, ''::text), 'autre'::text), NULLIF(NULLIF(e.lib_thema, ''::text), 'autre'::text), NULLIF(af.lib_thema, ''::text), 'autre'::text) AS lib_thema,
    o.montant_ht,
    o.montant_ttc,
    o.taux_tva,
    o.prestataire,
    o.ligne_budget_id,
    o.montant_initial,
    o.annee_initiale,
    o.montant_engage,
    o.montant_realise
   FROM bancarisation.occurrence o
     LEFT JOIN bancarisation.echeance e ON e.id = o.echeance_id
     LEFT JOIN bancarisation.action_fiche af ON af.projet_id = o.projet_id AND af.cle = e.action_cle;

-- ---------------------------------------------------------------
-- 4. v_budget_annuel
-- ---------------------------------------------------------------

CREATE OR REPLACE VIEW bancarisation.v_budget_annuel AS
SELECT
  projet_id,
  annee,
  count(*) FILTER (WHERE montant_ht IS NOT NULL)  AS nb_lignes_chiffrees,
  count(*)                                         AS nb_occurrences,
  coalesce(sum(montant_ht), 0)                     AS total_ht,
  coalesce(sum(montant_realise), 0)                AS total_ht_realise,
  coalesce(sum(montant_engage), 0)                 AS total_ht_engage
FROM bancarisation.occurrence
WHERE statut <> 'supprime'
GROUP BY projet_id, annee;

-- ---------------------------------------------------------------
-- 5. Delta par année
-- ---------------------------------------------------------------

CREATE OR REPLACE VIEW bancarisation.v_budget_delta_annuel AS
WITH courant AS (
  SELECT projet_id, annee,
         coalesce(sum(montant_ht), 0)      AS prevu,
         coalesce(sum(montant_engage), 0)  AS engage,
         coalesce(sum(montant_realise), 0) AS realise
  FROM bancarisation.occurrence
  WHERE statut <> 'supprime'
  GROUP BY projet_id, annee
),
initial AS (
  SELECT projet_id, annee_initiale AS annee,
         coalesce(sum(montant_initial), 0) AS initial
  FROM bancarisation.occurrence
  WHERE montant_initial IS NOT NULL AND annee_initiale IS NOT NULL
  GROUP BY projet_id, annee_initiale
)
SELECT
  coalesce(c.projet_id, i.projet_id) AS projet_id,
  coalesce(c.annee, i.annee)         AS annee,
  coalesce(i.initial, 0)             AS initial,
  coalesce(c.prevu, 0)               AS prevu,
  coalesce(c.engage, 0)              AS engage,
  coalesce(c.realise, 0)             AS realise,
  coalesce(c.prevu, 0) - coalesce(i.initial, 0) AS delta_prevu_initial
FROM courant c
FULL JOIN initial i ON i.projet_id = c.projet_id AND i.annee = c.annee;

-- ---------------------------------------------------------------
-- 6. Décomposition des écarts
-- ---------------------------------------------------------------

CREATE OR REPLACE VIEW bancarisation.v_budget_ecarts AS
SELECT
  projet_id,
  coalesce(annee_initiale, annee) AS annee_ref,
  coalesce(sum(montant_initial) FILTER (
    WHERE statut = 'supprime' AND montant_initial IS NOT NULL), 0) AS annule,
  coalesce(sum(montant_ht) FILTER (
    WHERE statut <> 'supprime' AND annee_initiale IS NOT NULL
      AND annee <> annee_initiale), 0) AS glisse_sortant,
  coalesce(sum(montant_ht) FILTER (
    WHERE statut <> 'supprime' AND montant_initial IS NULL), 0) AS ajoute,
  coalesce(sum(montant_ht - montant_initial) FILTER (
    WHERE statut <> 'supprime' AND montant_initial IS NOT NULL
      AND annee = annee_initiale), 0) AS revision_prix
FROM bancarisation.occurrence
GROUP BY projet_id, coalesce(annee_initiale, annee);
