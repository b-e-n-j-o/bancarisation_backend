-- =====================================================================
-- 028_rls.sql — KerERC : RLS sur tout le schéma bancarisation
-- ATTENTION : met fin à l'accès anonyme (démo ouverte). Appliquer sur dev d'abord.
-- La migration échoue (rollback complet) si une table reste sans RLS forcée.
-- =====================================================================
begin;

-- ---------------------------------------------------------------------
-- 0. Nettoyage
-- ---------------------------------------------------------------------
drop table if exists bancarisation.test;

-- ---------------------------------------------------------------------
-- 1. Rattachement des tables enfants (projet_id ou organisation_id dérivé du parent)
--    Arguments du trigger : colonne cible, puis paires (table parente, colonne FK).
--    La première FK non nulle gagne. La valeur envoyée par le client est toujours écrasée.
-- ---------------------------------------------------------------------
create or replace function prive.deriver_rattachement()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  v_cible  text := tg_argv[0];
  v_valeur uuid;
  v_fk     uuid;
  i        int  := 1;
begin
  while i < tg_nargs and v_valeur is null loop
    v_fk := (to_jsonb(new) ->> tg_argv[i + 1])::uuid;
    if v_fk is not null then
      execute format('select %I from bancarisation.%I where id = $1', v_cible, tg_argv[i])
        into v_valeur using v_fk;
    end if;
    i := i + 2;
  end loop;
  new := jsonb_populate_record(new, jsonb_build_object(v_cible, v_valeur));
  return new;
end $$;

-- Ordre important : les parents avant leurs enfants (credit_lot avant credit_vente, etc.)
do $$
declare
  cfg   jsonb;
  t     text;
  cible text;
  args  text;
begin
  for cfg in select value from jsonb_array_elements($j$[
    ["demande_message",         "projet_id", "demande", "demande_id"],
    ["action_fiche_arrete",     "projet_id", "action_fiche", "action_fiche_id", "arrete", "arrete_id"],
    ["arrete_prescription",     "projet_id", "arrete", "arrete_id", "action_fiche", "action_fiche_id"],
    ["constat_controle",        "projet_id", "action_fiche", "action_fiche_id", "occurrence", "occurrence_id"],
    ["credit_lot",              "projet_id", "site_agrement", "site_agrement_id"],
    ["credit_palier",           "projet_id", "credit_lot", "lot_id"],
    ["credit_vente",            "projet_id", "credit_lot", "lot_id"],
    ["constat_gain",            "projet_id", "credit_lot", "lot_id", "constat_controle", "constat_controle_id"],
    ["prescription_couverture", "projet_id", "arrete_prescription", "prescription_id", "echeance", "echeance_id"],
    ["plan_cao_groupe",         "projet_id", "plan_cao", "plan_id"],
    ["plan_cao_calque",         "projet_id", "plan_cao", "plan_id"],
    ["plan_cao_entite",         "projet_id", "plan_cao", "plan_id"],
    ["ug_surf_parcelles",       "projet_id", "unites_de_gestion_surf", "ug_id"],
    ["ug_lin_parcelles",        "projet_id", "unites_de_gestion_lin", "ug_id"],
    ["ug_pct_parcelles",        "projet_id", "unites_de_gestion_pct", "ug_id"],
    ["droit_documents",         "organisation_id", "droits_fonciers", "droit_id"],
    ["checklist_modele_etape",  "organisation_id", "checklist_modele", "modele_id"]
  ]$j$::jsonb) loop
    t     := cfg ->> 0;
    cible := cfg ->> 1;
    select string_agg(quote_literal(v), ', ' order by ord) into args
      from jsonb_array_elements_text(cfg) with ordinality as e(v, ord)
     where ord >= 2;

    execute format('alter table bancarisation.%I add column if not exists %I uuid', t, cible);
    execute format('create index if not exists %I on bancarisation.%I (%I)', t || '_' || cible || '_idx', t, cible);
    execute format('drop trigger if exists deriver_rattachement on bancarisation.%I', t);
    execute format('create trigger deriver_rattachement before insert or update on bancarisation.%I
                    for each row execute function prive.deriver_rattachement(%s)', t, args);
    -- Remplissage : le trigger recalcule la valeur sur chaque ligne
    execute format('update bancarisation.%I set %I = null', t, cible);
  end loop;
end $$;

-- ---------------------------------------------------------------------
-- 2. Fonctions complémentaires
-- ---------------------------------------------------------------------

-- Ajout : le créateur voit immédiatement son projet (nécessaire au RETURNING de l'insert)
create or replace function prive.projets_lisibles()
returns setof uuid language sql stable security definer set search_path = '' as $$
  select p.id
    from bancarisation.projets p
    join prive.mes_appartenances() a on a.organisation_id = p.organisation_id
   where a.role = 'admin' or a.acces_global is not null or p.cree_par = (select auth.uid())
  union
  select pa.projet_id
    from bancarisation.projet_acces pa
   where pa.utilisateur_id = (select auth.uid())
     and (pa.expire_le is null or pa.expire_le > now())
  union
  select pa.projet_id
    from bancarisation.projet_acces pa
    join prive.mes_appartenances() a on a.organisation_id = pa.organisation_id
   where pa.expire_le is null or pa.expire_le > now()
$$;

-- Organisations dont je peux voir la fiche : les miennes + celles liées à mes projets
create or replace function prive.organisations_lisibles()
returns setof uuid language sql stable security definer set search_path = '' as $$
  select organisation_id from prive.mes_appartenances()
  union
  select p.organisation_id from bancarisation.projets p
   where p.id in (select prive.projets_lisibles())
  union
  select pa.organisation_id from bancarisation.projet_acces pa
   where pa.organisation_id is not null
     and pa.projet_id in (select prive.projets_lisibles())
$$;

revoke all on function prive.deriver_rattachement(), prive.organisations_lisibles() from public, anon;
grant execute on function prive.projets_lisibles(), prive.organisations_lisibles() to authenticated;

-- Un projet ne change jamais d'organisation propriétaire côté utilisateur
create or replace function prive.figer_organisation_projet()
returns trigger language plpgsql set search_path = '' as $$
begin
  if new.organisation_id is distinct from old.organisation_id and current_user = 'authenticated' then
    raise exception 'Changement d''organisation propriétaire interdit';
  end if;
  return new;
end $$;

drop trigger if exists projets_figer_organisation on bancarisation.projets;
create trigger projets_figer_organisation
  before update on bancarisation.projets
  for each row execute function prive.figer_organisation_projet();

-- ---------------------------------------------------------------------
-- 3. Journal d'audit automatique sur partages et appartenances
-- ---------------------------------------------------------------------
create or replace function prive.journaliser()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  r     jsonb := to_jsonb(coalesce(new, old));
  v_org uuid;
begin
  if r ? 'projet_id' then   -- partage : organisation propriétaire du projet
    select organisation_id into v_org from bancarisation.projets where id = (r ->> 'projet_id')::uuid;
  else
    v_org := (r ->> 'organisation_id')::uuid;
  end if;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  values (
    (select auth.uid()),
    v_org,
    (r ->> 'projet_id')::uuid,
    tg_table_name || '.' || lower(tg_op),
    coalesce(r ->> 'utilisateur_id', r ->> 'organisation_id'),
    jsonb_build_object(
      'avant', case when tg_op <> 'INSERT' then to_jsonb(old) end,
      'apres', case when tg_op <> 'DELETE' then to_jsonb(new) end)
  );
  return coalesce(new, old);
end $$;

drop trigger if exists journaliser on bancarisation.projet_acces;
create trigger journaliser after insert or update or delete on bancarisation.projet_acces
  for each row execute function prive.journaliser();
drop trigger if exists journaliser on bancarisation.membre_organisation;
create trigger journaliser after insert or update or delete on bancarisation.membre_organisation
  for each row execute function prive.journaliser();

-- ---------------------------------------------------------------------
-- 4. Privilèges : plus rien pour anon, la RLS décide pour authenticated
-- ---------------------------------------------------------------------
revoke all on all tables    in schema bancarisation from anon;
revoke all on all sequences in schema bancarisation from anon;
alter default privileges in schema bancarisation revoke all on tables from anon;

revoke execute on function bancarisation.projets_visibles(uuid),
                           bancarisation.organisations_visibles(uuid),
                           bancarisation.organisations_sous(uuid)
  from public, anon, authenticated;

grant usage on schema bancarisation to authenticated;
grant select, insert, update, delete on all tables in schema bancarisation to authenticated;
grant usage, select on all sequences in schema bancarisation to authenticated;

-- Restrictions réappliquées après le grant global
revoke insert, update, delete on bancarisation.journal_audit from authenticated;
revoke insert, update, delete on bancarisation.profils from authenticated;
grant update (nom, prenom) on bancarisation.profils to authenticated;

-- ---------------------------------------------------------------------
-- 5. Politiques
-- ---------------------------------------------------------------------
create function pg_temp.politiques(t text, sel text, ecr text, sup text)
returns void language plpgsql as $$
declare op text;
begin
  execute format('alter table bancarisation.%I enable row level security', t);
  execute format('alter table bancarisation.%I force row level security', t);
  foreach op in array array['select','insert','update','delete'] loop
    execute format('drop policy if exists %I on bancarisation.%I', t || '_' || op, t);
  end loop;
  if sel is not null then
    execute format('create policy %I on bancarisation.%I for select to authenticated using (%s)',
                   t || '_select', t, sel);
  end if;
  if ecr is not null then
    execute format('create policy %I on bancarisation.%I for insert to authenticated with check (%s)',
                   t || '_insert', t, ecr);
    execute format('create policy %I on bancarisation.%I for update to authenticated using (%s) with check (%s)',
                   t || '_update', t, ecr, ecr);
  end if;
  if sup is not null then
    execute format('create policy %I on bancarisation.%I for delete to authenticated using (%s)',
                   t || '_delete', t, sup);
  end if;
end $$;

-- 5a. Tables projet standard : lecture = accès au projet, écriture/suppression = niveau 3
select pg_temp.politiques(t,
  'projet_id in (select prive.projets_lisibles())',
  'prive.niveau_projet(projet_id) >= 3',
  'prive.niveau_projet(projet_id) >= 3')
from unnest(array[
  'acte_dreal','action_etape','action_fiche','action_fiche_arrete','annotation_terrain',
  'arrete','arrete_prescription','bilan_suivi','cadastre_parcelle','cadastre_parcelle_ug',
  'constat_controle','constat_gain','couche_non_georeferencee','demande','demande_message',
  'documents','echeance','emprise_projet','export_geomce','extraction_import',
  'geometrie_mouvement','occurrence','plan_cao','plan_cao_calque','plan_cao_entite',
  'plan_cao_groupe','prescription_couverture','projet_contact','projet_geometries',
  'projet_metadata','projet_prestataire','rapport_bilan','rapport_suivi','satellite_captures',
  'site_agrement','ug_lin_parcelles','ug_pct_parcelles','ug_surf_parcelles',
  'unites_de_gestion_lin','unites_de_gestion_pct','unites_de_gestion_surf'
]) t;

-- Supprimer un document : gestion uniquement (matrice)
drop policy documents_delete on bancarisation.documents;
create policy documents_delete on bancarisation.documents for delete to authenticated
  using (prive.niveau_projet(projet_id) >= 4);

-- 5b. Finances : interne ou partage avec voir_finances
select pg_temp.politiques(t,
  'prive.voit_finances(projet_id)',
  'prive.voit_finances(projet_id) and prive.niveau_projet(projet_id) >= 3',
  'prive.voit_finances(projet_id) and prive.niveau_projet(projet_id) >= 3')
from unnest(array[
  'budget_baseline','budget_import','budget_mouvement','ligne_budget',
  'credit_lot','credit_palier','credit_vente'
]) t;

-- 5c. Interne seulement : jamais visible d'un invité externe
select pg_temp.politiques('note_interne',
  'prive.est_interne(projet_id)',
  'prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3',
  'prive.est_interne(projet_id) and prive.niveau_projet(projet_id) >= 3');

-- 5d. Niveau organisation
select pg_temp.politiques(t,
  'organisation_id in (select organisation_id from prive.mes_appartenances())',
  'organisation_id in (select organisation_id from prive.mes_appartenances())',
  'organisation_id in (select organisation_id from prive.mes_appartenances())')
from unnest(array['personnes','droits_fonciers','droit_documents']) t;

select pg_temp.politiques(t,
  'organisation_id in (select organisation_id from prive.mes_appartenances())',
  'prive.role_org(organisation_id) = ''admin''',
  'prive.role_org(organisation_id) = ''admin''')
from unnest(array['checklist_modele','checklist_modele_etape']) t;

-- Parcelles : rattachées à un projet, ou au portefeuille de l'organisation
select pg_temp.politiques('parcelles',
  '(projet_id is not null and projet_id in (select prive.projets_lisibles()))
   or (projet_id is null and organisation_id in (select organisation_id from prive.mes_appartenances()))',
  '(projet_id is not null and prive.niveau_projet(projet_id) >= 3)
   or (projet_id is null and organisation_id in (select organisation_id from prive.mes_appartenances()))',
  '(projet_id is not null and prive.niveau_projet(projet_id) >= 3)
   or (projet_id is null and organisation_id in (select organisation_id from prive.mes_appartenances()))');

-- 5e. Référentiels et annuaire global : lecture pour tous les connectés, écriture backend
select pg_temp.politiques(t, 'true', null, null)
from unnest(array[
  'ref_geomce_categorie','role_contact_ref','parametre_signal','prestataires',
  'geomce_communes','geomce_lin','geomce_pct','geomce_surf'
]) t;

-- 5f. Fermées (aucune politique) : membre (doublon probable, à supprimer après vérification)
select pg_temp.politiques('membre', null, null, null);

-- 5g. Projets
select pg_temp.politiques('projets', 'id in (select prive.projets_lisibles())', null, null);
create policy projets_insert on bancarisation.projets for insert to authenticated
  with check (prive.role_org(organisation_id) is not null);
create policy projets_update on bancarisation.projets for update to authenticated
  using (prive.niveau_projet(id) >= 3) with check (prive.niveau_projet(id) >= 3);
create policy projets_delete on bancarisation.projets for delete to authenticated
  using (prive.role_org(organisation_id) = 'admin');

-- 5h. Partages : visibles par le bénéficiaire et par qui peut partager ; gérés par l'org propriétaire
select pg_temp.politiques('projet_acces',
  'utilisateur_id = (select auth.uid())
   or organisation_id in (select organisation_id from prive.mes_appartenances())
   or prive.peut_partager(projet_id)',
  'prive.peut_partager(projet_id)',
  'prive.peut_partager(projet_id)');

-- 5i. Organisations, arbre, appartenances : lecture seule, écritures par le module admin du backend
select pg_temp.politiques('organisations', 'id in (select prive.organisations_lisibles())', null, null);
select pg_temp.politiques('organisation_closure',
  'ancetre_id in (select prive.organisations_lisibles())
   and descendant_id in (select prive.organisations_lisibles())', null, null);
select pg_temp.politiques('membre_organisation',
  'utilisateur_id = (select auth.uid())
   or organisation_id in (select organisation_id from prive.mes_appartenances())', null, null);

-- 5j. Profils : en plus du sien, ceux des collègues et des participants à mes projets
create policy profils_select_collegues on bancarisation.profils for select to authenticated
  using (
    utilisateur_id in (select m.utilisateur_id from bancarisation.membre_organisation m
                        where m.organisation_id in (select organisation_id from prive.mes_appartenances()))
    or utilisateur_id in (select pa.utilisateur_id from bancarisation.projet_acces pa
                           where pa.projet_id in (select prive.projets_lisibles()))
  );

-- 5k. Journaux
select pg_temp.politiques('journal_actions', 'prive.est_interne(projet_id)', null, null);
create policy journal_actions_insert on bancarisation.journal_actions for insert to authenticated
  with check (prive.niveau_projet(projet_id) >= 2);

drop policy if exists journal_audit_select on bancarisation.journal_audit;
create policy journal_audit_select on bancarisation.journal_audit for select to authenticated
  using (
    (organisation_id is not null and prive.role_org(organisation_id) = 'admin')
    or (projet_id is not null and prive.peut_partager(projet_id))
  );
grant select on bancarisation.journal_audit to authenticated;

-- ---------------------------------------------------------------------
-- 6. Vues : exécution avec les droits de l'appelant (sinon elles contournent la RLS)
-- ---------------------------------------------------------------------
do $$
declare v record;
begin
  for v in
    select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'bancarisation' and c.relkind = 'v'
  loop
    execute format('alter view bancarisation.%I set (security_invoker = true)', v.relname);
  end loop;

  for v in
    select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'bancarisation' and c.relkind = 'm'
  loop
    raise warning 'Vue matérialisée % : pas de RLS possible, à traiter à part', v.relname;
  end loop;
end $$;

-- ---------------------------------------------------------------------
-- 7. Garde-fou : aucune table sans RLS activée ET forcée
-- ---------------------------------------------------------------------
do $$
declare v_liste text;
begin
  select string_agg(c.relname, ', ') into v_liste
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'bancarisation' and c.relkind in ('r','p')
     and not (c.relrowsecurity and c.relforcerowsecurity);
  if v_liste is not null then
    raise exception 'Tables sans RLS forcée : %', v_liste;
  end if;
end $$;

commit;