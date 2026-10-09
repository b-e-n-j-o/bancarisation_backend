-- Accès interne (membre_organisation) : pas de qualité de partage.
-- La qualité ne vient que d'une ligne projet_acces.

create or replace function bancarisation.mes_droits_projets(p_ids uuid[] default null)
returns table (
  projet_id uuid,
  niveau int,
  interne boolean,
  finances boolean,
  partage boolean,
  qualite text
)
language sql stable security invoker set search_path = ''
as $$
  select p.id,
         prive.niveau_projet(p.id),
         prive.est_interne(p.id),
         prive.voit_finances(p.id),
         prive.peut_partager(p.id),
         case
           when prive.est_interne(p.id) then null
           else (
             select pa.qualite
               from bancarisation.projet_acces pa
              where pa.projet_id = p.id
                and (pa.expire_le is null or pa.expire_le > now())
                and (pa.utilisateur_id = auth.uid()
                     or pa.organisation_id in (
                          select a.organisation_id from prive.mes_appartenances() a))
              order by case pa.niveau
                         when 'gestionnaire' then 3
                         when 'contributeur' then 2
                         else 1
                       end desc
              limit 1)
         end
    from bancarisation.projets p
   where p_ids is null or p.id = any(p_ids);
$$;

insert into public.schema_migrations (version) values ('058_mes_droits_qualite_interne');

notify pgrst, 'reload schema';
