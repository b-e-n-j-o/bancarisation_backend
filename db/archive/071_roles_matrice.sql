-- 071_roles_matrice.sql — version appliquée dans Studio le 22 octobre 2026 (transaction et inscription incluses).
begin;

-- 071_roles_matrice.sql
-- Aligne la base sur la matrice des rôles (écarts trouvés par tests/roles.sql, 21 octobre).
--
--   A. Foncier (parcelles) : réservé au bureau propriétaire du projet, en lecture comme en écriture.
--   C. Bilans et rapports : un partenaire ne voit que les bilans validés et les rapports déposés.
--   E. Valider un bilan (statut 'valide') : niveau gestion (4) seulement.
--   D. Contacts du projet : écriture réservée au bureau.
--   B. Contacts partagés : opt-in par contact (partage_partenaires, faux par défaut),
--      lus par les partenaires via contacts_partages(), qui ne renvoie que des coordonnées
--      professionnelles (jamais adresse ni notes). La table personnes reste fermée aux partenaires.
--   G. Maître d'ouvrage et déposant : plus de lecture de la bibliothèque de documents,
--      sauf les PDF des bilans et rapports qu'ils peuvent déjà lire.
--
-- Suppression d'un projet : déjà réservée à l'admin d'organisation (projets_delete), inchangée.
--   H. Export (068-069) : search_path vide et même définition d'« admin » que le reste.
--
-- Pas de BEGIN/COMMIT : appliquer.sh ouvre la transaction. Dans Studio, l'entourer de
-- begin; … commit; et ajouter l'inscription dans schema_migrations (voir le message).

-- ---------------------------------------------------------------------
-- A. Foncier
-- ---------------------------------------------------------------------
drop policy if exists parcelles_select on bancarisation.parcelles;
drop policy if exists parcelles_insert on bancarisation.parcelles;
drop policy if exists parcelles_update on bancarisation.parcelles;
drop policy if exists parcelles_delete on bancarisation.parcelles;

create policy parcelles_select on bancarisation.parcelles
  for select to authenticated
  using (
    (projet_id is not null and prive.est_interne(projet_id))
    or (projet_id is null and organisation_id in (select organisation_id from prive.mes_appartenances()))
  );

create policy parcelles_insert on bancarisation.parcelles
  for insert to authenticated
  with check (
    (projet_id is not null and prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3)
    or (projet_id is null and organisation_id in (select organisation_id from prive.mes_appartenances()))
  );

create policy parcelles_update on bancarisation.parcelles
  for update to authenticated
  using (
    (projet_id is not null and prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3)
    or (projet_id is null and organisation_id in (select organisation_id from prive.mes_appartenances()))
  )
  with check (
    (projet_id is not null and prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3)
    or (projet_id is null and organisation_id in (select organisation_id from prive.mes_appartenances()))
  );

create policy parcelles_delete on bancarisation.parcelles
  for delete to authenticated
  using (
    (projet_id is not null and prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3)
    or (projet_id is null and organisation_id in (select organisation_id from prive.mes_appartenances()))
  );

-- ---------------------------------------------------------------------
-- C + E. Bilans (rapport_bilan) et rapports de suivi (rapport_suivi)
-- ---------------------------------------------------------------------
drop policy if exists rapport_bilan_select on bancarisation.rapport_bilan;
drop policy if exists rapport_bilan_insert on bancarisation.rapport_bilan;
drop policy if exists rapport_bilan_update on bancarisation.rapport_bilan;

create policy rapport_bilan_select on bancarisation.rapport_bilan
  for select to authenticated
  using (
    projet_id in (select prive.projets_lisibles())
    and (type_bilan = 'ecologique' or prive.voit_finances(projet_id))
    and (prive.est_interne(projet_id) or statut = 'valide')
  );

create policy rapport_bilan_insert on bancarisation.rapport_bilan
  for insert to authenticated
  with check (
    prive.niveau_projet(projet_id) >= 3
    and (type_bilan = 'ecologique' or prive.voit_finances(projet_id))
    and (statut <> 'valide' or prive.niveau_projet(projet_id) = 4)
  );

-- Modifier un brouillon : niveau 3. Le passer en 'valide' : niveau 4.
-- Un bilan déjà validé reste protégé par le trigger trg_proteger_rapport_valide.
create policy rapport_bilan_update on bancarisation.rapport_bilan
  for update to authenticated
  using (
    prive.niveau_projet(projet_id) >= 3
    and (type_bilan = 'ecologique' or prive.voit_finances(projet_id))
  )
  with check (
    prive.niveau_projet(projet_id) >= 3
    and (type_bilan = 'ecologique' or prive.voit_finances(projet_id))
    and (statut <> 'valide' or prive.niveau_projet(projet_id) = 4)
  );

drop policy if exists rapport_suivi_select on bancarisation.rapport_suivi;

create policy rapport_suivi_select on bancarisation.rapport_suivi
  for select to authenticated
  using (
    projet_id in (select prive.projets_lisibles())
    and (prive.est_interne(projet_id) or statut = 'depose')
  );

-- ---------------------------------------------------------------------
-- B + D. Contacts du projet
-- ---------------------------------------------------------------------
alter table bancarisation.projet_contact
  add column if not exists partage_partenaires boolean not null default false;

comment on column bancarisation.projet_contact.partage_partenaires is
  'Visible des partenaires du projet (maître d''ouvrage, instructeur…) via contacts_partages(). '
  'Faux par défaut : le bureau choisit qui il expose. Ne jamais partager un propriétaire ou un titulaire de droits fonciers.';

drop policy if exists projet_contact_select on bancarisation.projet_contact;
drop policy if exists projet_contact_insert on bancarisation.projet_contact;
drop policy if exists projet_contact_update on bancarisation.projet_contact;
drop policy if exists projet_contact_delete on bancarisation.projet_contact;

create policy projet_contact_select on bancarisation.projet_contact
  for select to authenticated
  using (
    projet_id in (select prive.projets_lisibles())
    and (prive.est_interne(projet_id) or partage_partenaires)
  );

create policy projet_contact_insert on bancarisation.projet_contact
  for insert to authenticated
  with check (prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3);

create policy projet_contact_update on bancarisation.projet_contact
  for update to authenticated
  using (prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3)
  with check (prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3);

create policy projet_contact_delete on bancarisation.projet_contact
  for delete to authenticated
  using (prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3);

-- Coordonnées professionnelles des contacts partagés d'un projet que l'on peut lire.
-- SECURITY DEFINER : traverse la RLS de personnes, mais ne renvoie que ces colonnes-là.
-- (La RLS filtre des lignes, pas des colonnes : ouvrir personnes aux partenaires
--  exposerait adresse et notes. D'où cette fonction plutôt qu'une politique.)
create or replace function bancarisation.contacts_partages(p_projet uuid)
returns table (
  projet_contact_id uuid,
  personne_id uuid,
  role_code text,
  role_precision text,
  principal boolean,
  nom text,
  prenom text,
  fonction text,
  email text,
  telephone text
)
language sql
stable
security definer
set search_path = ''
as $$
  select pc.id, p.id, pc.role_code, pc.role_precision, pc.principal,
         p.nom, p.prenom, p.fonction, p.email, p.telephone
    from bancarisation.projet_contact pc
    join bancarisation.personnes p on p.id = pc.personne_id
   where pc.projet_id = p_projet
     and pc.partage_partenaires
     and not p.archive
     and p_projet in (select prive.projets_lisibles())
   order by pc.principal desc, p.nom, p.prenom
$$;

revoke all on function bancarisation.contacts_partages(uuid) from public, anon;
grant execute on function bancarisation.contacts_partages(uuid) to authenticated;

-- ---------------------------------------------------------------------
-- G. Documents : maître d'ouvrage et déposant ne lisent pas la bibliothèque
-- ---------------------------------------------------------------------
-- Vrai si l'utilisateur n'est pas du bureau propriétaire et que TOUS ses accès actifs
-- au projet (personnels ou par son organisation) sont des accès restreints.
create or replace function prive.acces_restreint(p_projet uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  with mes_acces as (
    select pa.qualite
      from bancarisation.projet_acces pa
     where pa.projet_id = p_projet
       and (pa.expire_le is null or pa.expire_le > now())
       and (pa.utilisateur_id = (select auth.uid())
            or pa.organisation_id in (select organisation_id from prive.mes_appartenances()))
  )
  select not prive.est_interne(p_projet)
     and exists (select 1 from mes_acces)
     and not exists (
           select 1 from mes_acces
            where qualite is null or qualite not in ('maitre_ouvrage', 'prestataire'))
$$;

revoke all on function prive.acces_restreint(uuid) from public, anon;
grant execute on function prive.acces_restreint(uuid) to authenticated;

drop policy if exists documents_select on bancarisation.documents;

-- Les sous-requêtes sur rapport_suivi / rapport_bilan passent par leur propre RLS :
-- un accès restreint ne voit que les PDF des rapports déposés et des bilans validés.
create policy documents_select on bancarisation.documents
  for select to authenticated
  using (
    projet_id in (select prive.projets_lisibles())
    and (
      not prive.acces_restreint(projet_id)
      or exists (select 1 from bancarisation.rapport_suivi rs where rs.document_id = documents.id)
      or exists (select 1 from bancarisation.rapport_bilan rb where rb.document_id = documents.id)
    )
  );

-- ---------------------------------------------------------------------
-- H. Export (068-069) : durcissement, sans changer le comportement voulu
-- ---------------------------------------------------------------------
-- 1. search_path vide sur les fonctions SECURITY DEFINER, comme toutes les fonctions prive :
--    une fonction qui tourne avec les droits de postgres ne doit résoudre aucun nom
--    par le search_path (les corps qualifient déjà tout).
-- 2. peut_lire_archive_export passe par prive.mes_appartenances(), comme peut_exporter_projet :
--    même définition de « admin » partout (organisation active, portée branche comprise).
--    Avant, un admin d'une organisation suspendue gardait l'accès aux archives.

create or replace function bancarisation.peut_exporter_projet(p_projet_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
      from bancarisation.projets p
      join bancarisation.organisation_closure c
        on c.descendant_id = p.organisation_id
      join bancarisation.membre_organisation m
        on m.organisation_id = c.ancetre_id
       and m.utilisateur_id = (select auth.uid())
       and m.statut = 'actif'
       and m.role = 'admin'
       and (m.portee = 'branche' or c.profondeur = 0)
      join bancarisation.organisations o
        on o.id = m.organisation_id
       and o.statut = 'active'
     where p.id = p_projet_id
  );
$$;

create or replace function bancarisation.peut_lire_archive_export(p_organisation uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from prive.mes_appartenances() a
     where a.organisation_id = p_organisation
       and a.role = 'admin'
  );
$$;

create or replace function bancarisation.rattacher_export_transfert(p_transfert uuid, p_export uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update bancarisation.transfert_projet t
     set export_id = p_export
   where t.id = p_transfert
     and t.demande_par = (select auth.uid())
     and t.statut = 'en_attente'
     and exists (
       select 1 from bancarisation.export_projet e
        where e.id = p_export
          and e.projet_id = t.projet_id
          and e.demande_par = (select auth.uid())
     );
  if not found then
    raise exception 'Transfert ou export introuvable' using errcode = '42501';
  end if;
end;
$$;

-- create or replace garde les droits existants ; on les reconfirme par sécurité.
revoke all on function bancarisation.peut_exporter_projet(uuid) from public, anon;
revoke all on function bancarisation.peut_lire_archive_export(uuid) from public, anon;
revoke all on function bancarisation.rattacher_export_transfert(uuid, uuid) from public, anon;
grant execute on function bancarisation.peut_exporter_projet(uuid) to authenticated;
grant execute on function bancarisation.peut_lire_archive_export(uuid) to authenticated;
grant execute on function bancarisation.rattacher_export_transfert(uuid, uuid) to authenticated;

insert into public.schema_migrations (version) values ('071_roles_matrice');
commit;
notify pgrst, 'reload schema';
