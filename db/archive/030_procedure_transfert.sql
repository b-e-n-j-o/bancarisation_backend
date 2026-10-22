-- =====================================================================
-- 030_transfert_projet.sql — KerERC : transfert d'un projet vers une autre organisation
-- Demande par l'organisation cédante, acceptation par l'organisation repreneuse,
-- bascule atomique et journalisée. Aucun fichier à déplacer (chemins par projet_id).
-- =====================================================================
begin;

-- ---------------------------------------------------------------------
-- 1. Natures d'organisation : ajout ASL et autre porteur
-- ---------------------------------------------------------------------
alter table bancarisation.organisations drop constraint if exists organisations_nature_check;
alter table bancarisation.organisations add constraint organisations_nature_check
  check (nature in ('bureau_etudes','operateur','maitre_ouvrage','service_instructeur','asl','autre'));

-- ---------------------------------------------------------------------
-- 2. Qualité « ancien_proprietaire » pour la lecture conservée par le cédant
-- ---------------------------------------------------------------------
alter table bancarisation.projet_acces drop constraint projet_acces_role_check;
alter table bancarisation.projet_acces add constraint projet_acces_qualite_check
  check (qualite in ('operateur','maitre_ouvrage','bureau_etudes','controle','ancien_proprietaire'));

-- ---------------------------------------------------------------------
-- 3. Notes internes : rattachées à l'organisation autrice, pas au projet
--    Elles restent au cédant après un transfert ; chaque organisation
--    ayant accès à un projet partagé peut tenir ses propres notes.
-- ---------------------------------------------------------------------
create or replace function prive.organisation_auteur(p_projet uuid)
returns uuid language sql stable security definer set search_path = '' as $$
  select coalesce(
    -- membre de l'organisation propriétaire (ou de sa branche)
    (select p.organisation_id
       from bancarisation.projets p
       join prive.mes_appartenances() a on a.organisation_id = p.organisation_id
      where p.id = p_projet
      limit 1),
    -- sinon, l'organisation par laquelle j'ai accès au projet
    (select pa.organisation_id
       from bancarisation.projet_acces pa
       join prive.mes_appartenances() a on a.organisation_id = pa.organisation_id
      where pa.projet_id = p_projet
        and (pa.expire_le is null or pa.expire_le > now())
      order by prive.niveau_valeur(pa.niveau) desc
      limit 1))
$$;
revoke all on function prive.organisation_auteur(uuid) from public, anon;
grant execute on function prive.organisation_auteur(uuid) to authenticated;

alter table bancarisation.note_interne
  add column organisation_id uuid references bancarisation.organisations(id) on delete cascade;

update bancarisation.note_interne n
   set organisation_id = p.organisation_id
  from bancarisation.projets p
 where p.id = n.projet_id;

create index note_interne_organisation_idx on bancarisation.note_interne (organisation_id, projet_id);

create or replace function prive.note_interne_organisation()
returns trigger language plpgsql set search_path = '' as $$
begin
  if tg_op = 'INSERT' then
    if new.organisation_id is null then
      new.organisation_id := prive.organisation_auteur(new.projet_id);
    end if;
  else
    new.organisation_id := old.organisation_id;   -- une note ne change jamais d'organisation
  end if;
  return new;
end $$;

-- « zz_ » : s'exécute après le trigger existant qui déduit projet_id de l'occurrence
create trigger zz_note_interne_organisation
  before insert or update on bancarisation.note_interne
  for each row execute function prive.note_interne_organisation();

drop policy note_interne_select on bancarisation.note_interne;
drop policy note_interne_insert on bancarisation.note_interne;
drop policy note_interne_update on bancarisation.note_interne;
drop policy note_interne_delete on bancarisation.note_interne;

create policy note_interne_select on bancarisation.note_interne for select to authenticated
  using (organisation_id in (select organisation_id from prive.mes_appartenances())
         and projet_id in (select prive.projets_lisibles()));

create policy note_interne_insert on bancarisation.note_interne for insert to authenticated
  with check (organisation_id in (select organisation_id from prive.mes_appartenances())
              and prive.niveau_projet(projet_id) >= 3);

create policy note_interne_update on bancarisation.note_interne for update to authenticated
  using (organisation_id in (select organisation_id from prive.mes_appartenances())
         and prive.niveau_projet(projet_id) >= 3)
  with check (organisation_id in (select organisation_id from prive.mes_appartenances())
              and prive.niveau_projet(projet_id) >= 3);

create policy note_interne_delete on bancarisation.note_interne for delete to authenticated
  using (organisation_id in (select organisation_id from prive.mes_appartenances())
         and prive.niveau_projet(projet_id) >= 3);

-- ---------------------------------------------------------------------
-- 4. Table des transferts
-- ---------------------------------------------------------------------
create table bancarisation.transfert_projet (
  id                    uuid primary key default gen_random_uuid(),
  projet_id             uuid not null references bancarisation.projets(id) on delete cascade,
  projet_nom            text,   -- copie : le repreneur doit voir de quoi il s'agit avant d'avoir accès
  org_source            uuid not null references bancarisation.organisations(id),
  org_cible             uuid not null references bancarisation.organisations(id),
  statut                text not null default 'en_attente'
                        check (statut in ('en_attente','accepte','refuse','annule')),
  garder_lecture_source boolean not null default true,
  finances_source       boolean not null default false,
  commentaire           text,
  motif_refus           text,
  demande_par           uuid references auth.users(id) on delete set null,
  demande_le            timestamptz not null default now(),
  traite_par            uuid references auth.users(id) on delete set null,
  traite_le             timestamptz,
  check (org_source <> org_cible)
);

create unique index transfert_projet_un_en_attente
  on bancarisation.transfert_projet (projet_id) where statut = 'en_attente';
create index transfert_projet_cible_idx  on bancarisation.transfert_projet (org_cible, statut);
create index transfert_projet_source_idx on bancarisation.transfert_projet (org_source, statut);

alter table bancarisation.transfert_projet enable row level security;
alter table bancarisation.transfert_projet force row level security;
revoke all on bancarisation.transfert_projet from anon, authenticated;
grant select on bancarisation.transfert_projet to authenticated;   -- écritures uniquement via les fonctions

create policy transfert_projet_select on bancarisation.transfert_projet for select to authenticated
  using (prive.peut_partager(projet_id)
         or prive.role_org(org_source) = 'admin'
         or prive.role_org(org_cible)  = 'admin');

-- ---------------------------------------------------------------------
-- 5. Fonctions (appelables par l'API, vérifient elles-mêmes les droits)
-- ---------------------------------------------------------------------

-- Demande : admin de l'organisation propriétaire ou gestionnaire interne du projet
create or replace function bancarisation.demander_transfert(
  p_projet                uuid,
  p_org_cible             uuid,
  p_garder_lecture_source boolean default true,
  p_finances_source       boolean default false,
  p_commentaire           text    default null)
returns uuid language plpgsql security definer set search_path = '' as $$
declare
  v_source uuid;
  v_nom    text;
  v_id     uuid;
begin
  if (select auth.uid()) is null then
    raise exception 'Authentification requise' using errcode = '42501';
  end if;

  select organisation_id, nom into v_source, v_nom
    from bancarisation.projets where id = p_projet;

  if v_source is null or not prive.peut_partager(p_projet) then
    raise exception 'Droits insuffisants pour transférer ce projet' using errcode = '42501';
  end if;

  if p_org_cible = v_source then
    raise exception 'Le projet appartient déjà à cette organisation' using errcode = '22023';
  end if;

  if not exists (select 1 from bancarisation.organisations
                  where id = p_org_cible and statut = 'active' and not archivee) then
    raise exception 'Organisation cible introuvable ou inactive' using errcode = 'P0002';
  end if;

  insert into bancarisation.transfert_projet
    (projet_id, projet_nom, org_source, org_cible, garder_lecture_source, finances_source, commentaire, demande_par)
  values
    (p_projet, v_nom, v_source, p_org_cible, p_garder_lecture_source, p_finances_source, p_commentaire, (select auth.uid()))
  returning id into v_id;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  values ((select auth.uid()), v_source, p_projet, 'transfert.demande', p_org_cible::text,
          jsonb_build_object('transfert_id', v_id,
                             'garder_lecture_source', p_garder_lecture_source,
                             'finances_source', p_finances_source));
  return v_id;
end $$;

-- Acceptation : admin de l'organisation repreneuse. Bascule atomique.
create or replace function bancarisation.accepter_transfert(p_transfert uuid)
returns void language plpgsql security definer set search_path = '' as $$
declare
  t          bancarisation.transfert_projet;
  v_retires  int;
begin
  if (select auth.uid()) is null then
    raise exception 'Authentification requise' using errcode = '42501';
  end if;

  select * into t from bancarisation.transfert_projet where id = p_transfert for update;

  if t.id is null or t.statut <> 'en_attente'
     or coalesce(prive.role_org(t.org_cible), '') <> 'admin' then
    raise exception 'Transfert introuvable ou droits insuffisants' using errcode = '42501';
  end if;

  if (select organisation_id from bancarisation.projets where id = t.projet_id) is distinct from t.org_source then
    raise exception 'Le projet a changé de propriétaire depuis la demande' using errcode = '40001';
  end if;

  -- 1. Nouveau propriétaire (le trigger de gel laisse passer : la fonction ne s'exécute pas en « authenticated »)
  update bancarisation.projets
     set organisation_id = t.org_cible, updated_at = now()
   where id = t.projet_id;

  -- 2. Retrait des accès personnels des membres de l'organisation cédante (et de ses ancêtres)
  delete from bancarisation.projet_acces pa
   where pa.projet_id = t.projet_id
     and pa.utilisateur_id in (
       select m.utilisateur_id
         from bancarisation.membre_organisation m
         join bancarisation.organisation_closure c on c.ancetre_id = m.organisation_id
        where c.descendant_id = t.org_source);
  get diagnostics v_retires = row_count;

  -- 3. Un partage vers le repreneur n'a plus lieu d'être : il est propriétaire
  delete from bancarisation.projet_acces
   where projet_id = t.projet_id and organisation_id = t.org_cible;

  -- 4. Lecture conservée par le cédant (historique, responsabilité sur sa période)
  if t.garder_lecture_source then
    insert into bancarisation.projet_acces
      (projet_id, organisation_id, qualite, niveau, voir_finances, accorde_par)
    values
      (t.projet_id, t.org_source, 'ancien_proprietaire', 'lecteur', t.finances_source, (select auth.uid()))
    on conflict do nothing;
  end if;

  -- Les partages externes (maître d'ouvrage, DREAL...) sont conservés tels quels.

  -- 5. Clôture et journal, côté cédant et côté repreneur
  update bancarisation.transfert_projet
     set statut = 'accepte', traite_par = (select auth.uid()), traite_le = now()
   where id = t.id;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  select (select auth.uid()), o, t.projet_id, 'transfert.accepte', t.org_cible::text,
         jsonb_build_object('transfert_id', t.id, 'org_source', t.org_source, 'org_cible', t.org_cible,
                            'acces_personnels_retires', v_retires,
                            'lecture_conservee', t.garder_lecture_source)
    from unnest(array[t.org_source, t.org_cible]) as o;
end $$;

-- Refus : admin de l'organisation repreneuse
create or replace function bancarisation.refuser_transfert(p_transfert uuid, p_motif text default null)
returns void language plpgsql security definer set search_path = '' as $$
declare t bancarisation.transfert_projet;
begin
  select * into t from bancarisation.transfert_projet where id = p_transfert for update;
  if t.id is null or t.statut <> 'en_attente'
     or coalesce(prive.role_org(t.org_cible), '') <> 'admin' then
    raise exception 'Transfert introuvable ou droits insuffisants' using errcode = '42501';
  end if;

  update bancarisation.transfert_projet
     set statut = 'refuse', motif_refus = p_motif, traite_par = (select auth.uid()), traite_le = now()
   where id = t.id;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  select (select auth.uid()), o, t.projet_id, 'transfert.refuse', t.org_cible::text,
         jsonb_build_object('transfert_id', t.id, 'motif', p_motif)
    from unnest(array[t.org_source, t.org_cible]) as o;
end $$;

-- Annulation : côté cédant, tant que la demande est en attente
create or replace function bancarisation.annuler_transfert(p_transfert uuid)
returns void language plpgsql security definer set search_path = '' as $$
declare t bancarisation.transfert_projet;
begin
  select * into t from bancarisation.transfert_projet where id = p_transfert for update;
  if t.id is null or t.statut <> 'en_attente'
     or not (prive.peut_partager(t.projet_id) or coalesce(prive.role_org(t.org_source), '') = 'admin') then
    raise exception 'Transfert introuvable ou droits insuffisants' using errcode = '42501';
  end if;

  update bancarisation.transfert_projet
     set statut = 'annule', traite_par = (select auth.uid()), traite_le = now()
   where id = t.id;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  values ((select auth.uid()), t.org_source, t.projet_id, 'transfert.annule', t.org_cible::text,
          jsonb_build_object('transfert_id', t.id));
end $$;

revoke all on function
  bancarisation.demander_transfert(uuid, uuid, boolean, boolean, text),
  bancarisation.accepter_transfert(uuid),
  bancarisation.refuser_transfert(uuid, text),
  bancarisation.annuler_transfert(uuid)
from public, anon;

grant execute on function
  bancarisation.demander_transfert(uuid, uuid, boolean, boolean, text),
  bancarisation.accepter_transfert(uuid),
  bancarisation.refuser_transfert(uuid, text),
  bancarisation.annuler_transfert(uuid)
to authenticated;

commit;