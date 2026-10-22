-- 029_bilan_type.sql
begin;

alter table bancarisation.rapport_bilan
  add column type_bilan text not null default 'financier'
  check (type_bilan in ('ecologique','financier'));
-- par défaut « financier » : les bilans existants le sont, et c'est le choix le plus prudent

drop policy rapport_bilan_select on bancarisation.rapport_bilan;
drop policy rapport_bilan_insert on bancarisation.rapport_bilan;
drop policy rapport_bilan_update on bancarisation.rapport_bilan;
drop policy rapport_bilan_delete on bancarisation.rapport_bilan;

create policy rapport_bilan_select on bancarisation.rapport_bilan for select to authenticated
  using (projet_id in (select prive.projets_lisibles())
         and (type_bilan = 'ecologique' or prive.voit_finances(projet_id)));

create policy rapport_bilan_insert on bancarisation.rapport_bilan for insert to authenticated
  with check (prive.niveau_projet(projet_id) >= 3
              and (type_bilan = 'ecologique' or prive.voit_finances(projet_id)));

create policy rapport_bilan_update on bancarisation.rapport_bilan for update to authenticated
  using (prive.niveau_projet(projet_id) >= 3
         and (type_bilan = 'ecologique' or prive.voit_finances(projet_id)))
  with check (prive.niveau_projet(projet_id) >= 3
              and (type_bilan = 'ecologique' or prive.voit_finances(projet_id)));

create policy rapport_bilan_delete on bancarisation.rapport_bilan for delete to authenticated
  using (prive.niveau_projet(projet_id) >= 3
         and (type_bilan = 'ecologique' or prive.voit_finances(projet_id)));

commit;