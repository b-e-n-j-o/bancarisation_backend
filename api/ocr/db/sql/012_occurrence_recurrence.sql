-- 012_occurrence_recurrence.sql
-- Expose la règle de récurrence de l'échéance-source dans la vue calendrier.
-- Colonnes AJOUTÉES EN FIN (CREATE OR REPLACE VIEW ne peut pas réordonner).

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
    e.recurrence AS echeance_recurrence
   FROM bancarisation.occurrence o
     LEFT JOIN bancarisation.echeance e ON e.id = o.echeance_id
     LEFT JOIN bancarisation.action_fiche af ON af.projet_id = o.projet_id AND af.cle = e.action_cle
     LEFT JOIN bancarisation.prestataires p ON p.id = o.prestataire_id;
