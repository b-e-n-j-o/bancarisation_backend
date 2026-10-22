-- 067_anon_privileges_defaut.sql — appliqué dans Studio le 21 octobre 2026.
-- checks_structure.sql signalait des privilèges par défaut qui donnaient à anon
-- l'usage des futures séquences et l'exécution des futures fonctions de bancarisation.
begin;

alter default privileges for role postgres in schema bancarisation
  revoke all on sequences from anon;
alter default privileges for role postgres in schema bancarisation
  revoke all on functions from anon;

revoke all on all sequences in schema bancarisation from anon;
revoke all on all functions in schema bancarisation from anon;

insert into public.schema_migrations (version) values ('067_anon_privileges_defaut');
commit;
