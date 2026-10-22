-- 070_export_droits_backend.sql
-- kererc_backend (NOINHERIT) écrit le journal d'export sans SET ROLE :
-- il lui faut l'usage du schéma en propre. Droits de table et politiques : déjà posés par la 068.
begin;

grant usage on schema bancarisation to kererc_backend;

insert into public.schema_migrations (version) values ('070_export_droits_backend');

commit;