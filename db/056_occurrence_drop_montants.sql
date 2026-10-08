-- 056_occurrence_drop_montants.sql
-- Supprime les anciennes colonnes de montant sur occurrence.
--
-- NE PAS APPLIQUER tant que le backend déployé écrit encore ces colonnes
-- et que 055 n'est pas passé. Si un drop échoue sur une dépendance, une vue
-- lit encore l'ancienne colonne : corriger la vue, ne pas utiliser cascade.
-- La faille n'est fermée qu'après ce script. Pas de partage externe avant.

begin;

alter table bancarisation.occurrence
  drop column montant_ht,
  drop column montant_engage,
  drop column montant_realise,
  drop column montant_initial,
  drop column annee_initiale;

insert into public.schema_migrations (version) values ('056_occurrence_drop_montants');

commit;

notify pgrst, 'reload schema';
