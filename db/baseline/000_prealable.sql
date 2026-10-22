-- Généré par figer_baseline.sh (baseline_20261022). Rôles et extensions attendus par le schéma.
-- Les rôles Supabase (anon, authenticated, service_role…) viennent de l'image.
do $$ begin if not exists (select 1 from pg_roles where rolname = 'kererc_backend') then create role kererc_backend nologin noinherit; end if; end $$;
create extension if not exists postgis with schema public;
create extension if not exists pg_stat_statements with schema extensions;
create extension if not exists pg_net with schema extensions;
create extension if not exists pgcrypto with schema extensions;
create extension if not exists "uuid-ossp" with schema extensions;
create extension if not exists supabase_vault with schema vault;

-- kererc_backend passe en authenticated (SET LOCAL ROLE) pour que la RLS s'applique.
-- LOGIN et mot de passe : posés à la main sur chaque base, jamais dans le dépôt.
grant authenticated to kererc_backend;
