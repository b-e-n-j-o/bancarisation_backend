-- 029_budget_mouvement_periode.sql
-- Le trigger log_budget_mouvement ne traçait que montant / statut / année.
-- Un report intra-année (juin → septembre) ne touchait donc pas `annee` :
-- sans ce complément, décaler sans changer le statut ne laissait aucune trace.
--
-- On journalise aussi mois_debut / mois_fin. Le statut `repousse` n'est plus
-- posé automatiquement au décalage : l'occurrence reste planifiée, l'historique
-- porte la mémoire du report (bilan d'exercice + tiroir détail).

CREATE OR REPLACE FUNCTION bancarisation.log_budget_mouvement()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  v_motif text := current_setting('bancarisation.motif', true);
  v_par   text := current_setting('bancarisation.modifie_par', true);
BEGIN
  IF NEW.montant_ht IS DISTINCT FROM OLD.montant_ht THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'montant_ht',
            OLD.montant_ht::text, NEW.montant_ht::text, v_motif, v_par);
  END IF;

  IF NEW.montant_engage IS DISTINCT FROM OLD.montant_engage THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'montant_engage',
            OLD.montant_engage::text, NEW.montant_engage::text, v_motif, v_par);
  END IF;

  IF NEW.montant_realise IS DISTINCT FROM OLD.montant_realise THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'montant_realise',
            OLD.montant_realise::text, NEW.montant_realise::text, v_motif, v_par);
  END IF;

  IF NEW.statut IS DISTINCT FROM OLD.statut THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'statut',
            OLD.statut, NEW.statut, v_motif, v_par);
  END IF;

  IF NEW.annee IS DISTINCT FROM OLD.annee THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'annee',
            OLD.annee::text, NEW.annee::text, v_motif, v_par);
  END IF;

  IF NEW.mois_debut IS DISTINCT FROM OLD.mois_debut THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'mois_debut',
            OLD.mois_debut::text, NEW.mois_debut::text, v_motif, v_par);
  END IF;

  IF NEW.mois_fin IS DISTINCT FROM OLD.mois_fin THEN
    INSERT INTO bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    VALUES (NEW.id, NEW.projet_id, 'mois_fin',
            OLD.mois_fin::text, NEW.mois_fin::text, v_motif, v_par);
  END IF;

  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION bancarisation.log_budget_mouvement() IS
  'Historique append-only occurrence : montants, statut, année, mois_debut, mois_fin.';
