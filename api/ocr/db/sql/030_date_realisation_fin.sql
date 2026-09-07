-- 030_date_realisation_fin.sql
-- Amplitude de réalisation : une action peut s'étaler sur plusieurs jours / semaines.
-- date_realisation = début (déjà existant). date_realisation_fin = dernier jour
-- (NULL = journée unique). Exposé en fin de v_occurrence_calendrier (CREATE OR
-- REPLACE VIEW ne peut pas réordonner les colonnes).

ALTER TABLE bancarisation.occurrence
  ADD COLUMN IF NOT EXISTS date_realisation_fin date NULL;

ALTER TABLE bancarisation.occurrence
  DROP CONSTRAINT IF EXISTS occurrence_realisation_periode_chk;

ALTER TABLE bancarisation.occurrence
  ADD CONSTRAINT occurrence_realisation_periode_chk CHECK (
    date_realisation_fin IS NULL
    OR (
      date_realisation IS NOT NULL
      AND date_realisation_fin >= date_realisation
    )
  );

COMMENT ON COLUMN bancarisation.occurrence.date_realisation IS
  'Premier jour de réalisation effective (statut realise).';
COMMENT ON COLUMN bancarisation.occurrence.date_realisation_fin IS
  'Dernier jour de réalisation si l''opération s''étale ; NULL = journée unique.';

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
    o.montant_realise,
    o.prestataire_id,
    COALESCE(p.nom, o.prestataire) AS prestataire_nom,
    e.recurrence AS echeance_recurrence,
    o.date_realisation_fin
   FROM bancarisation.occurrence o
     LEFT JOIN bancarisation.echeance e ON e.id = o.echeance_id
     LEFT JOIN bancarisation.action_fiche af ON af.projet_id = o.projet_id AND af.cle = e.action_cle
     LEFT JOIN bancarisation.prestataires p ON p.id = o.prestataire_id;
