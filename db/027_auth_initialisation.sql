-- =====================================================================
-- 027_auth_structure.sql — KerERC : structure d'authentification et de droits
-- N'active PAS la RLS sur les tables métier (migration 028).
-- Prérequis : pg_dump du schéma bancarisation avant application.
-- =====================================================================
begin;

-- ---------------------------------------------------------------------
-- 0. Schéma privé (jamais exposé à PostgREST : ne pas l'ajouter à PGRST_DB_SCHEMAS)
-- ---------------------------------------------------------------------
create schema if not exists prive;
revoke all on schema prive from public, anon;
grant usage on schema prive to authenticated;   -- nécessaire pour appeler les fonctions depuis les politiques

-- ---------------------------------------------------------------------
-- 1. Organisations : nature métier + statut
-- ---------------------------------------------------------------------
alter table bancarisation.organisations
  add column nature text
    check (nature in ('bureau_etudes','operateur','maitre_ouvrage','service_instructeur')),
  add column statut text not null default 'active'
    check (statut in ('active','suspendue'));

-- ---------------------------------------------------------------------
-- 2. Profils (1 par compte auth)
-- ---------------------------------------------------------------------
create table bancarisation.profils (
  utilisateur_id   uuid primary key references auth.users(id) on delete cascade,
  nom              text,
  prenom           text,
  admin_plateforme boolean not null default false,
  cree_le          timestamptz not null default now()
);

alter table bancarisation.profils enable row level security;
alter table bancarisation.profils force row level security;

revoke all on bancarisation.profils from anon, authenticated;
grant select on bancarisation.profils to authenticated;
grant update (nom, prenom) on bancarisation.profils to authenticated;  -- admin_plateforme non modifiable

create policy profils_select_soi on bancarisation.profils
  for select to authenticated using (utilisateur_id = (select auth.uid()));
create policy profils_update_soi on bancarisation.profils
  for update to authenticated
  using (utilisateur_id = (select auth.uid()))
  with check (utilisateur_id = (select auth.uid()));

create or replace function prive.creer_profil()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  insert into bancarisation.profils (utilisateur_id) values (new.id)
  on conflict do nothing;
  return new;
end $$;

drop trigger if exists kererc_creer_profil on auth.users;
create trigger kererc_creer_profil
  after insert on auth.users
  for each row execute function prive.creer_profil();

insert into bancarisation.profils (utilisateur_id)
select id from auth.users on conflict do nothing;

-- ---------------------------------------------------------------------
-- 3. Appartenances : admin | membre + acces_global + statut
--    Reprise du comportement démo : chaque non-admin garde l'accès à tous
--    les projets de son org via acces_global ; l'admin réduira ensuite.
-- ---------------------------------------------------------------------
alter table bancarisation.membre_organisation
  add column acces_global text
    check (acces_global in ('lecteur','contributeur','gestionnaire')),
  add column statut text not null default 'actif'
    check (statut in ('invite','actif','suspendu')),
  add column invite_par uuid references auth.users(id) on delete set null;

update bancarisation.membre_organisation
   set acces_global = case role
         when 'lecteur'      then 'lecteur'
         when 'contributeur' then 'contributeur'
         when 'responsable'  then 'gestionnaire'
       end
 where role <> 'admin';

alter table bancarisation.membre_organisation drop constraint membre_organisation_role_check;
update bancarisation.membre_organisation set role = 'membre' where role <> 'admin';
alter table bancarisation.membre_organisation
  add constraint membre_organisation_role_check check (role in ('admin','membre')),
  alter column role set default 'membre',
  alter column statut set default 'invite';   -- les futures lignes naissent d'une invitation

-- FK vers auth.users : NOT VALID car la démo peut contenir des uuid orphelins.
-- Appliquée aux nouvelles lignes dès maintenant ; VALIDATE à l'étape 8 après nettoyage.
alter table bancarisation.membre_organisation
  add constraint membre_organisation_utilisateur_id_fkey
  foreign key (utilisateur_id) references auth.users(id) on delete cascade not valid;

create index if not exists membre_organisation_utilisateur_idx
  on bancarisation.membre_organisation (utilisateur_id) where statut = 'actif';

-- ---------------------------------------------------------------------
-- 4. Partages de projet : à une organisation OU à une personne
-- ---------------------------------------------------------------------
alter table bancarisation.projet_acces drop constraint projet_acces_pkey;

alter table bancarisation.projet_acces
  add column id uuid not null default gen_random_uuid(),
  add column utilisateur_id uuid references auth.users(id) on delete cascade,
  add column voir_finances boolean not null default false,
  add column expire_le timestamptz,
  add column accorde_par uuid references auth.users(id) on delete set null;

alter table bancarisation.projet_acces add primary key (id);
alter table bancarisation.projet_acces alter column organisation_id drop not null;

-- role (qualité de l'acteur) -> qualite ; optionnel pour un partage à une personne
alter table bancarisation.projet_acces rename column role to qualite;
alter table bancarisation.projet_acces alter column qualite drop not null;

-- droit (lecture|saisie) -> niveau (lecteur|contributeur|gestionnaire)
alter table bancarisation.projet_acces drop constraint projet_acces_droit_check;
alter table bancarisation.projet_acces rename column droit to niveau;
update bancarisation.projet_acces
   set niveau = case niveau when 'saisie' then 'contributeur' else 'lecteur' end;
alter table bancarisation.projet_acces
  add constraint projet_acces_niveau_check check (niveau in ('lecteur','contributeur','gestionnaire')),
  alter column niveau set default 'lecteur',
  add constraint projet_acces_un_beneficiaire check (num_nonnulls(organisation_id, utilisateur_id) = 1);

create unique index projet_acces_org_uniq
  on bancarisation.projet_acces (projet_id, organisation_id, coalesce(qualite, ''))
  where organisation_id is not null;
create unique index projet_acces_user_uniq
  on bancarisation.projet_acces (projet_id, utilisateur_id)
  where utilisateur_id is not null;
create index projet_acces_org_idx  on bancarisation.projet_acces (organisation_id);
create index projet_acces_user_idx on bancarisation.projet_acces (utilisateur_id);

-- Budget partagé avec la DREAL -> option du partage de qualité « controle »
update bancarisation.projet_acces a
   set voir_finances = true
  from bancarisation.projets p
 where p.id = a.projet_id and p.partager_budget_dreal and a.qualite = 'controle';
-- projets.partager_budget_dreal conservée tant que le front l'utilise ; suppression ultérieure.

-- ---------------------------------------------------------------------
-- 5. Projets : créateur
-- ---------------------------------------------------------------------
alter table bancarisation.projets
  add column cree_par uuid references auth.users(id) on delete set null;

-- ---------------------------------------------------------------------
-- 6. Journal d'audit (sécurité) — distinct de journal_actions (activité métier)
-- ---------------------------------------------------------------------
create table bancarisation.journal_audit (
  id              bigint generated always as identity primary key,
  horodatage      timestamptz not null default now(),
  acteur_id       uuid,          -- pas de FK : l'historique survit à la suppression du compte
  organisation_id uuid,
  projet_id       uuid,
  action          text not null, -- invitation, changement_role, suspension, partage, retrait_partage...
  cible           text,
  details         jsonb not null default '{}'
);
create index journal_audit_org_idx    on bancarisation.journal_audit (organisation_id, horodatage desc);
create index journal_audit_projet_idx on bancarisation.journal_audit (projet_id, horodatage desc);

alter table bancarisation.journal_audit enable row level security;
alter table bancarisation.journal_audit force row level security;
revoke all on bancarisation.journal_audit from anon, authenticated;
-- Insertion par le backend (module admin) ; lecture ouverte en 028 avec politiques.

create or replace function prive.interdire_modification()
returns trigger language plpgsql as $$
begin
  raise exception 'journal_audit est en écriture seule';
end $$;

create trigger journal_audit_immuable
  before update or delete on bancarisation.journal_audit
  for each row execute function prive.interdire_modification();
create trigger journal_audit_pas_de_truncate
  before truncate on bancarisation.journal_audit
  for each statement execute function prive.interdire_modification();

-- ---------------------------------------------------------------------
-- 7. Fonctions de droits (security definer, search_path vide)
-- ---------------------------------------------------------------------

-- Niveau numérique : gestionnaire 4, contributeur 3, lecteur 2
create or replace function prive.niveau_valeur(p_niveau text)
returns int language sql immutable set search_path = '' as $$
  select case p_niveau when 'gestionnaire' then 4 when 'contributeur' then 3 when 'lecteur' then 2 end
$$;

-- Mes appartenances actives, étendues aux descendants en portée « branche »
create or replace function prive.mes_appartenances()
returns table (organisation_id uuid, role text, acces_global text)
language sql stable security definer set search_path = '' as $$
  select c.descendant_id, m.role, m.acces_global
    from bancarisation.membre_organisation m
    join bancarisation.organisation_closure c
      on c.ancetre_id = m.organisation_id
     and (m.portee = 'branche' or c.profondeur = 0)
    join bancarisation.organisations o
      on o.id = m.organisation_id and o.statut = 'active'
   where m.utilisateur_id = (select auth.uid())
     and m.statut = 'actif'
$$;

-- Ensemble des projets lisibles
create or replace function prive.projets_lisibles()
returns setof uuid language sql stable security definer set search_path = '' as $$
  -- 1. org propriétaire : admin ou acces_global
  select p.id
    from bancarisation.projets p
    join prive.mes_appartenances() a on a.organisation_id = p.organisation_id
   where a.role = 'admin' or a.acces_global is not null
  union
  -- 2. partage personnel (inclut les projets attribués aux membres internes)
  select pa.projet_id
    from bancarisation.projet_acces pa
   where pa.utilisateur_id = (select auth.uid())
     and (pa.expire_le is null or pa.expire_le > now())
  union
  -- 3. partage à une de mes organisations
  select pa.projet_id
    from bancarisation.projet_acces pa
    join prive.mes_appartenances() a on a.organisation_id = pa.organisation_id
   where pa.expire_le is null or pa.expire_le > now()
$$;

-- Niveau effectif sur un projet : 4 gestion, 3 écriture, 2 lecture, 0 aucun
create or replace function prive.niveau_projet(p_projet uuid)
returns int language sql stable security definer set search_path = '' as $$
  select coalesce(max(n), 0) from (
    select case when a.role = 'admin' then 4 else prive.niveau_valeur(a.acces_global) end as n
      from bancarisation.projets p
      join prive.mes_appartenances() a on a.organisation_id = p.organisation_id
     where p.id = p_projet
    union all
    select prive.niveau_valeur(pa.niveau)
      from bancarisation.projet_acces pa
     where pa.projet_id = p_projet
       and pa.utilisateur_id = (select auth.uid())
       and (pa.expire_le is null or pa.expire_le > now())
    union all
    select prive.niveau_valeur(pa.niveau)
      from bancarisation.projet_acces pa
      join prive.mes_appartenances() a on a.organisation_id = pa.organisation_id
     where pa.projet_id = p_projet
       and (pa.expire_le is null or pa.expire_le > now())
  ) s
$$;

-- Interne : membre actif de l'org propriétaire (ou de sa branche) avec un accès au projet
create or replace function prive.est_interne(p_projet uuid)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
           select 1 from bancarisation.projets p
             join prive.mes_appartenances() a on a.organisation_id = p.organisation_id
            where p.id = p_projet)
     and prive.niveau_projet(p_projet) > 0
$$;

-- Finances : interne, ou partage avec voir_finances
create or replace function prive.voit_finances(p_projet uuid)
returns boolean language sql stable security definer set search_path = '' as $$
  select prive.est_interne(p_projet)
      or exists (
           select 1 from bancarisation.projet_acces pa
            where pa.projet_id = p_projet
              and pa.voir_finances
              and (pa.expire_le is null or pa.expire_le > now())
              and (pa.utilisateur_id = (select auth.uid())
                   or pa.organisation_id in (select organisation_id from prive.mes_appartenances())))
$$;

-- Partager / gérer les accès : gestion ET interne (un bénéficiaire ne re-partage pas)
create or replace function prive.peut_partager(p_projet uuid)
returns boolean language sql stable security definer set search_path = '' as $$
  select prive.niveau_projet(p_projet) = 4 and prive.est_interne(p_projet)
$$;

-- Rôle dans une organisation : 'admin', 'membre' ou null
create or replace function prive.role_org(p_org uuid)
returns text language sql stable security definer set search_path = '' as $$
  select case when bool_or(role = 'admin') then 'admin'
              when count(*) > 0 then 'membre' end
    from prive.mes_appartenances()
   where organisation_id = p_org
$$;

revoke all on all functions in schema prive from public, anon;
grant execute on function
  prive.niveau_valeur(text), prive.mes_appartenances(), prive.projets_lisibles(),
  prive.niveau_projet(uuid), prive.est_interne(uuid), prive.voit_finances(uuid),
  prive.peut_partager(uuid), prive.role_org(uuid)
to authenticated;

-- ---------------------------------------------------------------------
-- 8. Le créateur d'un projet en devient gestionnaire
-- ---------------------------------------------------------------------
create or replace function prive.createur_gestionnaire()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if (select auth.uid()) is not null then
    insert into bancarisation.projet_acces (projet_id, utilisateur_id, niveau, accorde_par)
    values (new.id, (select auth.uid()), 'gestionnaire', (select auth.uid()))
    on conflict do nothing;
  end if;
  return new;
end $$;

create or replace function prive.renseigner_createur()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.cree_par := coalesce(new.cree_par, (select auth.uid()));
  return new;
end $$;

create trigger projets_renseigner_createur
  before insert on bancarisation.projets
  for each row execute function prive.renseigner_createur();
create trigger projets_createur_gestionnaire
  after insert on bancarisation.projets
  for each row execute function prive.createur_gestionnaire();

commit;