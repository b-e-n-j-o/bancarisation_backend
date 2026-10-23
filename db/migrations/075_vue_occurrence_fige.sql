-- Expose le drapeau fige (migration 074) au calendrier.
-- Colonne ajoutée en fin de vue : CREATE OR REPLACE l'accepte.

CREATE OR REPLACE VIEW bancarisation.v_occurrence_calendrier WITH (security_invoker='true') AS
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
    f.montant_ht,
    f.montant_ttc,
    f.taux_tva,
    o.prestataire,
    o.ligne_budget_id,
    f.montant_initial,
    f.annee_initiale,
    f.montant_engage,
    f.montant_realise,
    o.prestataire_id,
    COALESCE(p.nom, o.prestataire) AS prestataire_nom,
    e.recurrence AS echeance_recurrence,
    o.date_realisation_fin,
    o.surface_m2,
    o.responsable_id,
    COALESCE(NULLIF(TRIM(BOTH FROM concat_ws(' '::text, resp.prenom, resp.nom)), ''::text), resp.nom) AS responsable_nom,
    o.fige
   FROM (((((bancarisation.occurrence o
     LEFT JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = o.id)))
     LEFT JOIN bancarisation.echeance e ON ((e.id = o.echeance_id)))
     LEFT JOIN bancarisation.action_fiche af ON (((af.projet_id = o.projet_id) AND (af.cle = e.action_cle))))
     LEFT JOIN bancarisation.prestataires p ON ((p.id = o.prestataire_id)))
     LEFT JOIN bancarisation.personnes resp ON ((resp.id = o.responsable_id)));
