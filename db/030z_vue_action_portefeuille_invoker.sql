-- Répare la fuite introduite par 047_v_action_portefeuille_definer.sql.
-- security_invoker = false faisait exécuter la vue avec les droits de son
-- propriétaire : tout utilisateur connecté voyait les actions de toutes
-- les organisations. La définition de 047 est alignée ; ce script corrige
-- une base où 047 a déjà été appliqué.
--
-- À appliquer sur supabase-dev avant tout compte client, après 030.

alter view bancarisation.v_action_portefeuille set (security_invoker = true);

revoke all on bancarisation.v_action_portefeuille from public, anon;
grant select on bancarisation.v_action_portefeuille to authenticated;

notify pgrst, 'reload schema';

create table if not exists public.schema_migrations (
  version text primary key,
  appliquee_le timestamptz default now()
);

insert into public.schema_migrations (version) values ('030z_vue_action_portefeuille_invoker');
