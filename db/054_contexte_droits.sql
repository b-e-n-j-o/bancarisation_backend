-- 054 — contexte et droits exposés au front, et lien personne ↔ compte.
-- Numéro 054 : 048 à 053 sont déjà pris dans api/ocr/db/sql.
-- À appliquer après 043 (colonne personnes.utilisateur_id) et après 047.
--
-- prive.mes_appartenances() renvoie (organisation_id, role, acces_global),
-- pas un setof uuid. Les appels ci-dessous sélectionnent organisation_id.
-- security invoker : la RLS de l'appelant filtre les projets.

begin;

-- ---------------------------------------------------------------------
-- Lien facultatif compte ↔ fiche d'annuaire, par organisation.
-- L'auteur d'une action reste auth.uid(), affiché via profils.
-- ---------------------------------------------------------------------

alter table bancarisation.personnes
  drop constraint if exists personnes_utilisateur_fkey;

drop index if exists bancarisation.personnes_utilisateur_id_uidx;

alter table bancarisation.personnes
  add constraint personnes_utilisateur_fkey
  foreign key (utilisateur_id) references auth.users(id) on delete set null;

create unique index personnes_org_utilisateur_uidx
  on bancarisation.personnes (organisation_id, utilisateur_id)
  where utilisateur_id is not null;

comment on column bancarisation.personnes.utilisateur_id is
  'Compte auth.users facultatif. Unique par organisation : le même compte peut avoir une fiche dans plusieurs annuaires. L''audit (qui a fait quoi) passe par auth.uid() et profils, pas par cette colonne.';

-- ---------------------------------------------------------------------
-- Exposition
-- ---------------------------------------------------------------------

create or replace function bancarisation.mon_contexte()
returns jsonb
language sql stable security invoker set search_path = ''
as $$
  select jsonb_build_object(
    'utilisateur_id', auth.uid(),
    'profil', (
      select jsonb_build_object(
               'nom', p.nom,
               'prenom', p.prenom,
               'admin_plateforme', p.admin_plateforme)
        from bancarisation.profils p
       where p.utilisateur_id = auth.uid()),
    'appartenances', coalesce((
      select jsonb_agg(jsonb_build_object(
               'organisation_id', o.id,
               'nom', o.nom,
               'nature', o.nature,
               'type', o.type,
               'parent_id', o.parent_id,
               'role', m.role,
               'portee', m.portee,
               'acces_global', m.acces_global) order by o.nom)
        from bancarisation.membre_organisation m
        join bancarisation.organisations o on o.id = m.organisation_id
       where m.utilisateur_id = auth.uid()
         and m.statut = 'actif'
         and o.statut = 'active'), '[]'::jsonb)
  );
$$;

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
         (select pa.qualite
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
    from bancarisation.projets p
   where p_ids is null or p.id = any(p_ids);
$$;

revoke all on function bancarisation.mon_contexte() from public, anon;
revoke all on function bancarisation.mes_droits_projets(uuid[]) from public, anon;
grant execute on function bancarisation.mon_contexte() to authenticated;
grant execute on function bancarisation.mes_droits_projets(uuid[]) to authenticated;

commit;

notify pgrst, 'reload schema';
