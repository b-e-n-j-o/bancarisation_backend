-- Plusieurs responsables par occurrence.
-- Le premier reste recopié dans occurrence.responsable_id pour les vues existantes.
-- Une personne ajoutée ici est aussi rattachée aux contacts du projet
-- (côté applicatif) si elle ne l'est pas déjà.

create table if not exists bancarisation.occurrence_responsable (
  id            uuid primary key default gen_random_uuid(),
  occurrence_id uuid not null references bancarisation.occurrence(id) on delete cascade,
  projet_id     uuid not null references bancarisation.projets(id) on delete cascade,
  personne_id   uuid not null references bancarisation.personnes(id) on delete cascade,
  ordre         integer not null default 0,
  created_at    timestamptz not null default now(),
  constraint occurrence_responsable_unique unique (occurrence_id, personne_id)
);

create index if not exists occurrence_responsable_occ_idx
  on bancarisation.occurrence_responsable (occurrence_id, ordre);
create index if not exists occurrence_responsable_projet_idx
  on bancarisation.occurrence_responsable (projet_id);
create index if not exists occurrence_responsable_personne_idx
  on bancarisation.occurrence_responsable (personne_id);

comment on table bancarisation.occurrence_responsable is
  'Personnes en charge d''une occurrence. Liées aux contacts du projet.';

create or replace function bancarisation.tg_occurrence_responsable_before()
returns trigger language plpgsql as $$
begin
  if new.projet_id is null then
    select o.projet_id into new.projet_id
    from bancarisation.occurrence o
    where o.id = new.occurrence_id;
  end if;
  return new;
end $$;

drop trigger if exists occurrence_responsable_before on bancarisation.occurrence_responsable;
create trigger occurrence_responsable_before
  before insert or update on bancarisation.occurrence_responsable
  for each row execute function bancarisation.tg_occurrence_responsable_before();

insert into bancarisation.occurrence_responsable (occurrence_id, personne_id, ordre)
select o.id, o.responsable_id, 0
from bancarisation.occurrence o
where o.responsable_id is not null
on conflict (occurrence_id, personne_id) do nothing;

grant select, insert, update, delete on bancarisation.occurrence_responsable to authenticated;

alter table bancarisation.occurrence_responsable enable row level security;
alter table bancarisation.occurrence_responsable force row level security;

drop policy if exists occurrence_responsable_select on bancarisation.occurrence_responsable;
drop policy if exists occurrence_responsable_insert on bancarisation.occurrence_responsable;
drop policy if exists occurrence_responsable_update on bancarisation.occurrence_responsable;
drop policy if exists occurrence_responsable_delete on bancarisation.occurrence_responsable;

create policy occurrence_responsable_select on bancarisation.occurrence_responsable
  for select to authenticated
  using (projet_id in (select prive.projets_lisibles()));

create policy occurrence_responsable_insert on bancarisation.occurrence_responsable
  for insert to authenticated
  with check (prive.niveau_projet(projet_id) >= 3);

create policy occurrence_responsable_update on bancarisation.occurrence_responsable
  for update to authenticated
  using (prive.niveau_projet(projet_id) >= 3)
  with check (prive.niveau_projet(projet_id) >= 3);

create policy occurrence_responsable_delete on bancarisation.occurrence_responsable
  for delete to authenticated
  using (prive.niveau_projet(projet_id) >= 3);
