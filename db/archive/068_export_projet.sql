-- 068_export_projet.sql
-- Journal et droit d'export complet d'un projet (réversibilité).
-- Les écritures de statut sont faites par kererc_backend, sans SET ROLE :
-- la RLS est forcée, donc une policy explicite pour ce rôle est nécessaire.
-- Le bucket exports-projet est privé. Le plafond global FILE_SIZE_LIMIT du
-- storage self-hosted doit être au moins égal à file_size_limit (2 Gio) :
-- la valeur du bucket ne peut pas dépasser le plafond du service.

begin;

create table bancarisation.export_projet (
  id              uuid primary key default gen_random_uuid(),
  projet_id       uuid not null references bancarisation.projets(id) on delete cascade,
  organisation_id uuid not null references bancarisation.organisations(id),
  demande_par     uuid not null references auth.users(id),
  options         jsonb not null default '{}'::jsonb,
  statut          text not null default 'en_attente'
                  check (statut in (
                    'en_attente','en_cours','termine',
                    'termine_avec_avertissements','echec','expire'
                  )),
  progression     jsonb not null default '{}'::jsonb,
  storage_path    text,
  taille_octets   bigint,
  sha256          text,
  manifest        jsonb,
  erreur          text,
  created_at      timestamptz not null default now(),
  termine_le      timestamptz,
  expire_le       timestamptz,
  nb_telechargements int not null default 0,
  dernier_telechargement_le timestamptz
);

create index export_projet_projet_idx
  on bancarisation.export_projet (projet_id, created_at desc);

-- Un seul export actif par projet. Le 409 du routeur s'appuie dessus.
create unique index export_projet_un_seul_actif
  on bancarisation.export_projet (projet_id)
  where statut in ('en_attente', 'en_cours');

comment on table bancarisation.export_projet is
  'Journal des exports complets. La ligne reste après expiration du ZIP.';
comment on column bancarisation.export_projet.organisation_id is
  'Organisation propriétaire du projet au moment de la demande.';
comment on column bancarisation.export_projet.sha256 is
  'Empreinte SHA-256 du ZIP. Non modifiable par authenticated.';

-- Admin de l'organisation propriétaire, y compris un admin d'un ancêtre
-- en portée « branche ». Pas d'accès via projet_acces, pas d'admin plateforme.
create or replace function bancarisation.peut_exporter_projet(p_projet_id uuid)
returns boolean
language sql
stable
security definer
set search_path = bancarisation, public
as $$
  select exists (
    select 1
      from bancarisation.projets p
      join bancarisation.organisation_closure c
        on c.descendant_id = p.organisation_id
      join bancarisation.membre_organisation m
        on m.organisation_id = c.ancetre_id
       and m.utilisateur_id = auth.uid()
       and m.statut = 'actif'
       and m.role = 'admin'
       and (m.portee = 'branche' or c.profondeur = 0)
      join bancarisation.organisations o
        on o.id = m.organisation_id
       and o.statut = 'active'
     where p.id = p_projet_id
  );
$$;

revoke all on function bancarisation.peut_exporter_projet(uuid) from public, anon;
grant execute on function bancarisation.peut_exporter_projet(uuid) to authenticated;

alter table bancarisation.export_projet enable row level security;
alter table bancarisation.export_projet force row level security;

revoke all on bancarisation.export_projet from public, anon, authenticated;

grant select, insert on bancarisation.export_projet to authenticated;
grant select, update on bancarisation.export_projet to kererc_backend;

create policy export_projet_select on bancarisation.export_projet
  for select to authenticated
  using (bancarisation.peut_exporter_projet(projet_id));

create policy export_projet_insert on bancarisation.export_projet
  for insert to authenticated
  with check (
    bancarisation.peut_exporter_projet(projet_id)
    and demande_par = auth.uid()
    and organisation_id = (
      select p.organisation_id from bancarisation.projets p where p.id = projet_id
    )
  );

-- authenticated : ni update ni delete (le sha256 ne doit pas être réécrit via REST).
create policy export_projet_backend_select on bancarisation.export_projet
  for select to kererc_backend
  using (true);

create policy export_projet_backend_update on bancarisation.export_projet
  for update to kererc_backend
  using (true)
  with check (true);

insert into storage.buckets (id, name, public, file_size_limit)
values ('exports-projet', 'exports-projet', false, 2147483648)
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit;

insert into public.schema_migrations (version) values ('068_export_projet');

commit;

notify pgrst, 'reload schema';
