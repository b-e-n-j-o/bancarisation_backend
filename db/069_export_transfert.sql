-- 069_export_transfert.sql
-- L'archive lancée à la demande de transfert reste lisible par l'admin
-- de l'organisation qui l'a demandée, même après l'acceptation.

begin;

alter table bancarisation.transfert_projet
  add column if not exists export_id uuid references bancarisation.export_projet(id);

comment on column bancarisation.transfert_projet.export_id is
  'Export complet lancé à la création de la demande, au nom de l''organisation cédante.';

-- Vrai pour tout export enregistré au nom de l'organisation, pas seulement
-- l'archive du transfert. Les exports ordinaires expirent à 7 jours.
create or replace function bancarisation.peut_lire_archive_export(p_organisation uuid)
returns boolean
language sql
stable
security definer
set search_path = bancarisation, public
as $$
  select exists (
    select 1
      from bancarisation.membre_organisation m
     where m.organisation_id = p_organisation
       and m.utilisateur_id = auth.uid()
       and m.statut = 'actif'
       and m.role = 'admin'
  );
$$;

revoke all on function bancarisation.peut_lire_archive_export(uuid) from public, anon;
grant execute on function bancarisation.peut_lire_archive_export(uuid) to authenticated;

drop policy if exists export_projet_select on bancarisation.export_projet;
create policy export_projet_select on bancarisation.export_projet
  for select to authenticated
  using (
    bancarisation.peut_exporter_projet(projet_id)
    or bancarisation.peut_lire_archive_export(organisation_id)
  );

-- Écritures du transfert : uniquement via des fonctions. Celle-ci ne
-- change que le lien vers l'archive, et seulement pour le demandeur.
create or replace function bancarisation.rattacher_export_transfert(
  p_transfert uuid,
  p_export uuid
) returns void
language plpgsql
security definer
set search_path = bancarisation, public
as $$
begin
  update bancarisation.transfert_projet t
     set export_id = p_export
   where t.id = p_transfert
     and t.demande_par = auth.uid()
     and t.statut = 'en_attente'
     and exists (
       select 1 from bancarisation.export_projet e
        where e.id = p_export
          and e.projet_id = t.projet_id
          and e.demande_par = auth.uid()
     );
  if not found then
    raise exception 'Transfert ou export introuvable' using errcode = '42501';
  end if;
end;
$$;

revoke all on function bancarisation.rattacher_export_transfert(uuid, uuid) from public, anon;
grant execute on function bancarisation.rattacher_export_transfert(uuid, uuid) to authenticated;

insert into public.schema_migrations (version) values ('069_export_transfert');

commit;

notify pgrst, 'reload schema';
