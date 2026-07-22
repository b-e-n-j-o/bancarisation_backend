-- 017_bilan_sans_brouillon.sql
-- Un bilan généré est définitif pour le dossier (supprimable).
-- Plus de cycle brouillon → validé côté produit : on génère en statut 'valide'
-- et on retire la protection DELETE qui bloquait la suppression.

-- Autorise la suppression des bilans déjà générés (anciens + nouveaux).
DROP TRIGGER IF EXISTS trg_proteger_rapport_valide_del ON bancarisation.rapport_bilan;
DROP FUNCTION IF EXISTS bancarisation.proteger_rapport_valide_delete();

-- Harmonise l'existant : tout bilan archivé est traité comme généré.
UPDATE bancarisation.rapport_bilan
SET
  statut = 'valide',
  valide_le = COALESCE(valide_le, genere_le, now()),
  valide_par = COALESCE(valide_par, genere_par, 'migration-017')
WHERE statut = 'brouillon';
