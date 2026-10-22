-- 072_import_projet.sql
-- Journal de réimport d'un export KerERC. Nouveau projet uniquement.
-- Les triggers d'historique se taisent pendant la transaction d'import.
-- Format migrations/ : pas de begin/commit ni d'inscription, appliquer.sh s'en charge.


create table bancarisation.import_projet (
  id               uuid primary key default gen_random_uuid(),
  organisation_id  uuid not null references bancarisation.organisations(id),
  demande_par      uuid not null references auth.users(id),
  statut           text not null default 'televerse'
                   check (statut in ('televerse','analyse','pret','en_cours','termine','echec')),
  storage_path     text,
  sha256           text,
  manifest_source  jsonb,
  rapport          jsonb,
  projet_cree_id   uuid references bancarisation.projets(id),
  erreur           text,
  created_at       timestamptz not null default now(),
  termine_le       timestamptz
);

create index import_projet_org_idx
  on bancarisation.import_projet (organisation_id, created_at desc);

create table bancarisation.import_projet_id_map (
  import_id   uuid not null references bancarisation.import_projet(id) on delete cascade,
  table_nom   text not null,
  ancien_id   text not null,
  nouvel_id   text not null,
  primary key (import_id, table_nom, ancien_id)
);

comment on table bancarisation.import_projet is
  'Réimport d''un ZIP kererc-export-projet. Crée toujours un nouveau projet.';

create or replace function bancarisation.peut_importer_dans_organisation(p_org uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
      from bancarisation.organisation_closure c
      join bancarisation.membre_organisation m
        on m.organisation_id = c.ancetre_id
       and m.utilisateur_id = (select auth.uid())
       and m.statut = 'actif'
       and m.role = 'admin'
       and (m.portee = 'branche' or c.profondeur = 0)
      join bancarisation.organisations o
        on o.id = m.organisation_id
       and o.statut = 'active'
     where c.descendant_id = p_org
  );
$$;

revoke all on function bancarisation.peut_importer_dans_organisation(uuid) from public, anon;
grant execute on function bancarisation.peut_importer_dans_organisation(uuid) to authenticated;

alter table bancarisation.import_projet enable row level security;
alter table bancarisation.import_projet force row level security;
alter table bancarisation.import_projet_id_map enable row level security;
alter table bancarisation.import_projet_id_map force row level security;

revoke all on bancarisation.import_projet from public, anon, authenticated;
revoke all on bancarisation.import_projet_id_map from public, anon, authenticated;

grant select, insert on bancarisation.import_projet to authenticated;
grant select, update on bancarisation.import_projet to kererc_backend;
grant select, insert on bancarisation.import_projet_id_map to kererc_backend;

create policy import_projet_select on bancarisation.import_projet
  for select to authenticated
  using (bancarisation.peut_importer_dans_organisation(organisation_id));

create policy import_projet_insert on bancarisation.import_projet
  for insert to authenticated
  with check (
    bancarisation.peut_importer_dans_organisation(organisation_id)
    and demande_par = (select auth.uid())
  );

create policy import_projet_backend_select on bancarisation.import_projet
  for select to kererc_backend using (true);

create policy import_projet_backend_update on bancarisation.import_projet
  for update to kererc_backend using (true) with check (true);

-- Pas de FOR ALL (refusé par les contrôles) : le worker lit et ajoute, rien d'autre.
create policy import_map_backend_select on bancarisation.import_projet_id_map
  for select to kererc_backend using (true);

create policy import_map_backend_insert on bancarisation.import_projet_id_map
  for insert to kererc_backend with check (true);

insert into storage.buckets (id, name, public, file_size_limit)
values ('imports-projet', 'imports-projet', false, 2147483648)
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit;

-- Historique budgétaire : ne pas rejouer les triggers pendant l'import.
create or replace function bancarisation.log_budget_mouvement()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_motif text := current_setting('bancarisation.motif', true);
  v_par   text := current_setting('bancarisation.modifie_par', true);
begin
  if current_setting('kererc.import_en_cours', true) = 'on' then
    return new;
  end if;
  if new.statut is distinct from old.statut then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'statut', old.statut, new.statut, v_motif, v_par);
  end if;
  if new.annee is distinct from old.annee then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'annee', old.annee::text, new.annee::text, v_motif, v_par);
  end if;
  if new.mois_debut is distinct from old.mois_debut then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'mois_debut', old.mois_debut::text, new.mois_debut::text, v_motif, v_par);
  end if;
  if new.mois_fin is distinct from old.mois_fin then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'mois_fin', old.mois_fin::text, new.mois_fin::text, v_motif, v_par);
  end if;
  return new;
end $$;

create or replace function bancarisation.log_occurrence_finance()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if current_setting('kererc.import_en_cours', true) = 'on' then
    return new;
  end if;
  if tg_op = 'INSERT' then
    return new;
  end if;
  if new.montant_ht is distinct from old.montant_ht then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_ht',
            old.montant_ht::text, new.montant_ht::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.montant_engage is distinct from old.montant_engage then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_engage',
            old.montant_engage::text, new.montant_engage::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.montant_realise is distinct from old.montant_realise then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_realise',
            old.montant_realise::text, new.montant_realise::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.montant_ttc is distinct from old.montant_ttc then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_ttc',
            old.montant_ttc::text, new.montant_ttc::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.taux_tva is distinct from old.taux_tva then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'taux_tva',
            old.taux_tva::text, new.taux_tva::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  return new;
end $$;



