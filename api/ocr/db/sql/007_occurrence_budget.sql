-- Budget opérationnel porté par l'occurrence (source de vérité unique,
-- partagée entre tableur, calendrier et liste). ligne_budget reste la
-- table de staging/provenance issue du pipeline LLM.
--
-- Les colonnes ont pu être créées manuellement ; IF NOT EXISTS les rend idempotentes.
-- La vue calendrier DOIT exposer ces champs pour que le front les lise.

ALTER TABLE bancarisation.occurrence
  ADD COLUMN IF NOT EXISTS montant_ht numeric NULL,
  ADD COLUMN IF NOT EXISTS montant_ttc numeric NULL,
  ADD COLUMN IF NOT EXISTS taux_tva numeric NULL,
  ADD COLUMN IF NOT EXISTS prestataire text NULL,
  ADD COLUMN IF NOT EXISTS ligne_budget_id uuid NULL
    REFERENCES bancarisation.ligne_budget (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS occurrence_ligne_budget_idx
  ON bancarisation.occurrence USING btree (ligne_budget_id);

CREATE OR REPLACE VIEW bancarisation.v_budget_annuel AS
SELECT
  projet_id,
  annee,
  count(*) FILTER (WHERE montant_ht IS NOT NULL) AS nb_lignes_chiffrees,
  count(*)                                        AS nb_occurrences,
  coalesce(sum(montant_ht), 0)                    AS total_ht,
  coalesce(sum(montant_ht) FILTER (WHERE statut IN ('realise', 'acheve')), 0) AS total_ht_realise
FROM bancarisation.occurrence
GROUP BY projet_id, annee;

-- Exposer les champs budget dans la vue consommée par GET /occurrences.
-- CREATE OR REPLACE n'ajoute des colonnes qu'en FIN de SELECT : garder
-- l'ordre existant (jusqu'à lib_thema) et coller les 5 colonnes budget après.
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
    o.ligne_budget_id
   FROM bancarisation.occurrence o
     LEFT JOIN bancarisation.echeance e ON e.id = o.echeance_id
     LEFT JOIN bancarisation.action_fiche af ON af.projet_id = o.projet_id AND af.cle = e.action_cle;
