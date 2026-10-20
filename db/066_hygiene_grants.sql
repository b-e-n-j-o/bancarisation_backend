-- Hygiène post-audit RLS. Aucune policy.
-- invitation n'est lue et écrite que par le module admin, en rôle postgres
-- (api/admin/service.py, _connect / get_database_url). Pas via connect_utilisateur.
-- schema_migrations n'est pas lu par l'API.

alter table bancarisation.invitation force row level security;
revoke all on bancarisation.invitation from anon, authenticated;

revoke all on public.schema_migrations from anon, authenticated;
