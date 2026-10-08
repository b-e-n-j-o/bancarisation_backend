-- =====================================================================
-- tests_isolation.sql — KerERC : rejoue la matrice des droits contre la vraie base
-- Tout se fait dans une transaction annulée à la fin : rien ne persiste.
-- Exécuter avec un rôle qui contourne la RLS (postgres avec bypassrls, ou supabase_admin) :
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f tests_isolation.sql
-- Le premier « ÉCHEC » arrête le script.
-- =====================================================================
begin;

-- ---------------------------------------------------------------------
-- Outils
-- ---------------------------------------------------------------------
create schema tests;
grant usage on schema tests to authenticated;

create function tests.en_tant_que(p_uid uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',
    json_build_object('sub', p_uid, 'role', 'authenticated', 'aal', 'aal2')::text, true);
  perform set_config('role', 'authenticated', true);
end $$;

create function tests.ok(p_cond boolean, p_msg text) returns void language plpgsql as $$
begin
  if p_cond is distinct from true then raise exception 'ÉCHEC : %', p_msg; end if;
  raise notice 'ok : %', p_msg;
end $$;

create function tests.echoue(p_sql text, p_msg text) returns void language plpgsql as $$
begin
  begin
    execute p_sql;
  exception when others then
    raise notice 'ok : % (%)', p_msg, sqlerrm;
    return;
  end;
  raise exception 'ÉCHEC : % (aucune erreur levée)', p_msg;
end $$;

grant execute on all functions in schema tests to authenticated;

-- ---------------------------------------------------------------------
-- Jeu de données
-- ---------------------------------------------------------------------
-- Organisations : A et B (bureaux d'études), M (maître d'ouvrage)
insert into bancarisation.organisations (id, nom, nature) values
  ('00000000-0000-0000-0000-00000000000a', 'Test Org A', 'bureau_etudes'),
  ('00000000-0000-0000-0000-00000000000b', 'Test Org B', 'bureau_etudes'),
  ('00000000-0000-0000-0000-00000000000c', 'Test Org M', 'maitre_ouvrage');

-- Utilisateurs (le trigger crée les profils)
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-0000-0000-0000000000a1', 'admin.a@test.local',    'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000a2', 'membre.a1@test.local',  'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000a3', 'membre.a0@test.local',  'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000a4', 'suspendu.a@test.local', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000b1', 'admin.b@test.local',    'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000c1', 'membre.m@test.local',   'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000e1', 'externe@test.local',    'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000e2', 'expire@test.local',     'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000f1', 'plateforme@test.local', 'authenticated', 'authenticated');

update bancarisation.profils set admin_plateforme = true
 where utilisateur_id = '00000000-0000-0000-0000-0000000000f1';

insert into bancarisation.membre_organisation (utilisateur_id, organisation_id, role, acces_global, statut) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000000a', 'admin',  null,      'actif'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-00000000000a', 'membre', null,      'actif'),
  ('00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-00000000000a', 'membre', null,      'actif'),
  ('00000000-0000-0000-0000-0000000000a4', '00000000-0000-0000-0000-00000000000a', 'membre', 'lecteur', 'suspendu'),
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-00000000000b', 'admin',  null,      'actif'),
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-00000000000c', 'membre', null,      'actif');

-- Projets : PA1, PA2 chez A ; PB1 chez B
insert into bancarisation.projets (id, organisation_id, nom) values
  ('00000000-0000-0000-0000-0000000001a1', '00000000-0000-0000-0000-00000000000a', 'Test PA1'),
  ('00000000-0000-0000-0000-0000000001a2', '00000000-0000-0000-0000-00000000000a', 'Test PA2'),
  ('00000000-0000-0000-0000-0000000001b1', '00000000-0000-0000-0000-00000000000b', 'Test PB1');

insert into bancarisation.occurrence (id, projet_id, annee, code, titre, categorie)
values
  ('00000000-0000-0000-0000-0000000002a1', '00000000-0000-0000-0000-0000000001a1', 2026, 'T-FIN', 'Test finance', 'compensation'),
  ('00000000-0000-0000-0000-0000000002a2', '00000000-0000-0000-0000-0000000001a1', 2026, 'T-FIN2', 'Test finance écriture', 'compensation');

-- Partages
insert into bancarisation.projet_acces (projet_id, utilisateur_id, organisation_id, qualite, niveau, expire_le) values
  ('00000000-0000-0000-0000-0000000001a1', '00000000-0000-0000-0000-0000000000a2', null, null, 'contributeur', null),
  ('00000000-0000-0000-0000-0000000001a1', '00000000-0000-0000-0000-0000000000e1', null, null, 'lecteur', null),
  ('00000000-0000-0000-0000-0000000001a1', '00000000-0000-0000-0000-0000000000e2', null, null, 'lecteur', now() - interval '1 day'),
  ('00000000-0000-0000-0000-0000000001a1', null, '00000000-0000-0000-0000-00000000000c', 'maitre_ouvrage', 'lecteur', null),
  ('00000000-0000-0000-0000-0000000001b1', null, '00000000-0000-0000-0000-00000000000c', 'maitre_ouvrage', 'lecteur', null);

-- ---------------------------------------------------------------------
-- Admin de A
-- ---------------------------------------------------------------------
select tests.en_tant_que('00000000-0000-0000-0000-0000000000a1');
select tests.ok((select count(*) from bancarisation.projets) = 2, 'admin A voit les 2 projets de A');
select tests.ok(prive.niveau_projet('00000000-0000-0000-0000-0000000001a2') = 4, 'admin A : niveau gestion');
select tests.ok((select count(*) from bancarisation.projets where id = '00000000-0000-0000-0000-0000000001b1') = 0,
                'admin A ne voit pas PB1');
reset role;

-- ---------------------------------------------------------------------
-- Membre de A attribué à PA1 (contributeur)
-- ---------------------------------------------------------------------
select tests.en_tant_que('00000000-0000-0000-0000-0000000000a2');
select tests.ok((select count(*) from bancarisation.projets) = 1, 'membre A1 voit seulement PA1');
select tests.ok(prive.niveau_projet('00000000-0000-0000-0000-0000000001a1') = 3, 'membre A1 : niveau écriture');
select tests.ok(prive.voit_finances('00000000-0000-0000-0000-0000000001a1'), 'membre A1 interne : voit les finances');
select tests.echoue(
  $q$update bancarisation.profils set admin_plateforme = true where utilisateur_id = '00000000-0000-0000-0000-0000000000a2'$q$,
  'pas d''auto-promotion admin plateforme');
select tests.echoue(
  $q$insert into bancarisation.membre_organisation (utilisateur_id, organisation_id, role, statut)
     values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-00000000000b', 'admin', 'actif')$q$,
  'impossible de s''ajouter à une organisation');
select tests.echoue(
  $q$insert into bancarisation.projet_acces (projet_id, utilisateur_id, niveau)
     values ('00000000-0000-0000-0000-0000000001a1', '00000000-0000-0000-0000-0000000000a3', 'lecteur')$q$,
  'un contributeur ne partage pas');
reset role;

-- ---------------------------------------------------------------------
-- Membre de A sans attribution
-- ---------------------------------------------------------------------
select tests.en_tant_que('00000000-0000-0000-0000-0000000000a3');
select tests.ok((select count(*) from bancarisation.projets) = 0, 'membre A0 sans attribution ne voit rien');
reset role;

-- ---------------------------------------------------------------------
-- Invité externe en lecture sur PA1
-- ---------------------------------------------------------------------
select tests.en_tant_que('00000000-0000-0000-0000-0000000000e1');
select tests.ok((select count(*) from bancarisation.projets) = 1, 'externe voit seulement PA1');
select tests.ok(not prive.voit_finances('00000000-0000-0000-0000-0000000001a1'), 'externe : pas de finances');
select tests.ok(not prive.est_interne('00000000-0000-0000-0000-0000000001a1'), 'externe : pas interne');
select tests.echoue(
  $q$insert into bancarisation.projet_acces (projet_id, utilisateur_id, niveau)
     values ('00000000-0000-0000-0000-0000000001a1', '00000000-0000-0000-0000-0000000000a3', 'lecteur')$q$,
  'un bénéficiaire ne re-partage pas');
select tests.echoue(
  $q$insert into bancarisation.projets (organisation_id, nom)
     values ('00000000-0000-0000-0000-00000000000a', 'Intrus')$q$,
  'externe ne crée pas de projet chez A');
reset role;

-- ---------------------------------------------------------------------
-- Accès expiré, membre suspendu, admin plateforme
-- ---------------------------------------------------------------------
select tests.en_tant_que('00000000-0000-0000-0000-0000000000e2');
select tests.ok((select count(*) from bancarisation.projets) = 0, 'accès expiré : plus rien');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000a4');
select tests.ok((select count(*) from bancarisation.projets) = 0, 'membre suspendu : plus rien');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000f1');
select tests.ok((select count(*) from bancarisation.projets) = 0, 'admin plateforme : aucune donnée projet');
reset role;

-- ---------------------------------------------------------------------
-- Admin de B
-- ---------------------------------------------------------------------
select tests.en_tant_que('00000000-0000-0000-0000-0000000000b1');
select tests.ok((select count(*) from bancarisation.projets) = 1, 'admin B voit seulement PB1');
select tests.ok((select count(*) from bancarisation.projet_acces
                  where projet_id = '00000000-0000-0000-0000-0000000001a1') = 0,
                'admin B ne voit pas les partages de A');
reset role;

-- ---------------------------------------------------------------------
-- Maître d'ouvrage M : voit PA1 (de A) et PB1 (de B), rien d'autre
-- ---------------------------------------------------------------------
select tests.en_tant_que('00000000-0000-0000-0000-0000000000c1');
select tests.ok((select count(*) from bancarisation.projets) = 2, 'M voit les 2 projets partagés');
select tests.ok((select count(*) from bancarisation.projets
                  where id = '00000000-0000-0000-0000-0000000001a2') = 0, 'M ne voit pas PA2');
select tests.ok((select count(*) from bancarisation.mes_droits_projets()) = 2, 'M : deux lignes de droits');
select tests.ok((select niveau = 2 and not interne and not finances and not partage
                   and qualite = 'maitre_ouvrage'
                   from bancarisation.mes_droits_projets()
                  where projet_id = '00000000-0000-0000-0000-0000000001a1'),
                'M / PA1 : lecture partagée, sans finances');
select tests.ok(jsonb_array_length(bancarisation.mon_contexte()->'appartenances') = 1,
                'M : une appartenance');
reset role;

-- Départ de M : perte immédiate des accès
update bancarisation.membre_organisation set statut = 'suspendu'
 where utilisateur_id = '00000000-0000-0000-0000-0000000000c1';
select tests.en_tant_que('00000000-0000-0000-0000-0000000000c1');
select tests.ok((select count(*) from bancarisation.projets) = 0, 'membre sorti de M : plus rien');
select tests.ok(jsonb_array_length(bancarisation.mon_contexte()->'appartenances') = 0,
                'membre sorti de M : zéro appartenance');
select tests.ok((select count(*) from bancarisation.mes_droits_projets()) = 0,
                'membre sorti de M : zéro projet dans mes_droits_projets');
reset role;

-- ---------------------------------------------------------------------
-- Transfert de PA2 : A -> B
-- ---------------------------------------------------------------------
-- Note interne de A sur PA2 (si note_interne exige d'autres colonnes, les ajouter ici)
insert into bancarisation.note_interne (projet_id, organisation_id, contenu)
values ('00000000-0000-0000-0000-0000000001a2', '00000000-0000-0000-0000-00000000000a', 'Note interne de A');

select tests.en_tant_que('00000000-0000-0000-0000-0000000000a2');
select tests.echoue(
  $q$select bancarisation.demander_transfert('00000000-0000-0000-0000-0000000001a2', '00000000-0000-0000-0000-00000000000b')$q$,
  'un membre sans droit de gestion ne demande pas de transfert');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000a1');
select bancarisation.demander_transfert('00000000-0000-0000-0000-0000000001a2', '00000000-0000-0000-0000-00000000000b');
select tests.ok((select count(*) from bancarisation.transfert_projet
                  where projet_id = '00000000-0000-0000-0000-0000000001a2' and statut = 'en_attente') = 1,
                'demande de transfert enregistrée');
select tests.echoue(
  $q$select bancarisation.accepter_transfert((select id from bancarisation.transfert_projet
       where projet_id = '00000000-0000-0000-0000-0000000001a2' and statut = 'en_attente'))$q$,
  'le cédant ne peut pas accepter lui-même');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000b1');
select tests.ok((select count(*) from bancarisation.projets) = 1, 'B ne voit pas PA2 avant acceptation');
select tests.ok((select projet_nom from bancarisation.transfert_projet
                  where projet_id = '00000000-0000-0000-0000-0000000001a2' and statut = 'en_attente') = 'Test PA2',
                'B voit la demande et le nom du projet');
select tests.echoue(
  $q$update bancarisation.projets set organisation_id = '00000000-0000-0000-0000-00000000000a'
     where id = '00000000-0000-0000-0000-0000000001b1'$q$,
  'changer directement l''organisation d''un projet est interdit');
select bancarisation.accepter_transfert((select id from bancarisation.transfert_projet
  where projet_id = '00000000-0000-0000-0000-0000000001a2' and statut = 'en_attente'));
select tests.ok((select count(*) from bancarisation.projets) = 2, 'B voit PA2 après acceptation');
select tests.ok(prive.niveau_projet('00000000-0000-0000-0000-0000000001a2') = 4, 'admin B gère PA2');
select tests.ok(prive.voit_finances('00000000-0000-0000-0000-0000000001a2'), 'B reçoit l''historique financier');
select tests.ok((select count(*) from bancarisation.note_interne
                  where projet_id = '00000000-0000-0000-0000-0000000001a2') = 0,
                'B ne lit pas les notes internes de A');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000a1');
select tests.ok(prive.niveau_projet('00000000-0000-0000-0000-0000000001a2') = 2, 'A garde la lecture seule');
select tests.ok(not prive.voit_finances('00000000-0000-0000-0000-0000000001a2'), 'A ne voit plus les finances');
select tests.ok((select count(*) from bancarisation.note_interne
                  where projet_id = '00000000-0000-0000-0000-0000000001a2') = 1,
                'A garde ses notes internes');
select tests.echoue(
  $q$insert into bancarisation.projet_acces (projet_id, utilisateur_id, niveau)
     values ('00000000-0000-0000-0000-0000000001a2', '00000000-0000-0000-0000-0000000000a3', 'lecteur')$q$,
  'A ne peut plus partager PA2');
reset role;

select tests.ok((select count(*) from bancarisation.journal_audit
                  where projet_id = '00000000-0000-0000-0000-0000000001a2' and action like 'transfert.%') = 3,
                'transfert journalisé (demande, puis acceptation côté A et côté B)');

-- ---------------------------------------------------------------------
-- Journal d'audit et structure
-- ---------------------------------------------------------------------
select tests.ok((select count(*) from bancarisation.journal_audit
                  where projet_id = '00000000-0000-0000-0000-0000000001a1') >= 4,
                'les partages sont journalisés');
select tests.echoue('delete from bancarisation.journal_audit', 'journal : suppression refusée même au propriétaire');
select tests.echoue('update bancarisation.journal_audit set action = ''x''', 'journal : modification refusée');

select tests.ok(not exists (
  select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'bancarisation' and c.relkind in ('r','p')
     and not (c.relrowsecurity and c.relforcerowsecurity)),
  'toutes les tables ont la RLS activée et forcée');

select tests.ok(not exists (
  select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'bancarisation' and c.relkind = 'v'
     and not coalesce(c.reloptions @> array['security_invoker=true'], false)),
  'toutes les vues en security_invoker');

select tests.ok(coalesce((
  select c.reloptions @> array['security_invoker=true']
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'bancarisation' and c.relname = 'v_action_portefeuille'
), false),
  'v_action_portefeuille est security_invoker (pas de lecture inter-organisations)');

-- ---------------------------------------------------------------------
-- Contrôle des politiques : rien d'ancien, rien d'ouvert, chaque catégorie protégée
-- ---------------------------------------------------------------------
select tests.ok(not exists (
  select 1 from pg_policies where schemaname = 'bancarisation'
     and coalesce(qual, '') || coalesce(with_check, '')
         ~ '(peut_lire_projet|peut_ecrire_projet|peut_lire_interne|role_courant|organisation_courante)'),
  'aucune politique n''utilise les anciennes fonctions');

select tests.ok(not exists (
  select 1 from pg_policies where schemaname = 'bancarisation'
     and coalesce(qual, '') || coalesce(with_check, '') ilike '%auth.uid() IS NULL%'),
  'aucune politique n''ouvre l''accès sans connexion');

select tests.ok(not exists (
  select 1 from pg_policies where schemaname = 'bancarisation' and cmd = 'ALL'),
  'aucune politique « ALL » héritée');

select tests.ok(not exists (
  select 1 from pg_policies where schemaname = 'bancarisation'
     and roles <> array['authenticated']::name[]),
  'toutes les politiques sont réservées aux utilisateurs connectés');

select tests.ok(not exists (
  select 1 from pg_policies where schemaname = 'bancarisation'
     and (qual = 'true' or with_check = 'true')
     and tablename not in ('ref_geomce_categorie','role_contact_ref','parametre_signal','prestataires',
                           'geomce_communes','geomce_lin','geomce_pct','geomce_surf')),
  'lecture libre réservée aux référentiels');

select tests.ok(not exists (
  select 1 from pg_policies where schemaname = 'bancarisation'
     and tablename in ('budget_baseline','budget_import','budget_mouvement','ligne_budget',
                       'credit_lot','credit_palier','credit_vente')
     and coalesce(qual, with_check) not like '%voit_finances%'),
  'toutes les politiques des tables financières passent par voit_finances');

select tests.ok(not exists (
  select 1 from pg_policies where schemaname = 'bancarisation'
     and tablename = 'note_interne'
     and coalesce(qual, with_check) not like '%mes_appartenances%'),
  'note_interne limitée à l''organisation autrice');

select tests.ok(not exists (
  select 1 from pg_policies where schemaname = 'bancarisation'
     and tablename = 'rapport_bilan'
     and coalesce(qual, with_check) not like '%voit_finances%'),
  'bilans financiers protégés par voit_finances');

select tests.ok(not exists (
  select 1 from information_schema.role_table_grants
   where grantee = 'anon' and table_schema = 'bancarisation'),
  'anon n''a aucun privilège sur le schéma');

-- ---------------------------------------------------------------------
-- mon_contexte / mes_droits_projets
-- ---------------------------------------------------------------------
select tests.en_tant_que('00000000-0000-0000-0000-0000000000a1');
select tests.ok((select count(*) from bancarisation.mes_droits_projets()) = 2, 'admin A : deux projets');
select tests.ok((select niveau = 4 and interne and finances and partage and qualite is null
                   from bancarisation.mes_droits_projets()
                  where projet_id = '00000000-0000-0000-0000-0000000001a1'),
                'admin A / PA1 : gestion interne');
select tests.ok((select niveau = 2 and not interne and not finances and not partage
                   from bancarisation.mes_droits_projets()
                  where projet_id = '00000000-0000-0000-0000-0000000001a2'),
                'admin A / PA2 après transfert : lecture seule, sans finances');
select tests.ok((select count(*) from bancarisation.mes_droits_projets(
                   array['00000000-0000-0000-0000-0000000001b1']::uuid[])) = 0,
                'projet invisible : aucune ligne, pas un niveau 0');
select tests.ok(jsonb_array_length(bancarisation.mon_contexte()->'appartenances') = 1,
                'admin A : une appartenance');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000a2');
-- Avant le transfert, A1 ne voyait que PA1. La lecture conservée est un
-- partage à l'organisation A : tout membre actif de A voit aussi PA2.
select tests.ok((select count(*) from bancarisation.mes_droits_projets()) = 2,
                'membre A1 : PA1 et la lecture conservée sur PA2');
select tests.ok((select niveau = 3 and interne and finances and not partage
                   from bancarisation.mes_droits_projets()
                  where projet_id = '00000000-0000-0000-0000-0000000001a1'),
                'membre A1 / PA1 : écriture interne, pas de partage');
select tests.ok((select niveau = 2 and not interne and not finances and not partage
                   and qualite = 'ancien_proprietaire'
                   from bancarisation.mes_droits_projets()
                  where projet_id = '00000000-0000-0000-0000-0000000001a2'),
                'membre A1 / PA2 : lecture de l''ancienne organisation, sans finances');
select tests.ok(jsonb_array_length(bancarisation.mon_contexte()->'appartenances') = 1,
                'membre A1 : une appartenance');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000a3');
select tests.ok((select count(*) from bancarisation.mes_droits_projets()) = 1,
                'membre sans attribution : seulement la lecture conservée sur PA2');
select tests.ok((select niveau = 2 and not interne and not finances and not partage
                   and qualite = 'ancien_proprietaire'
                   from bancarisation.mes_droits_projets()
                  where projet_id = '00000000-0000-0000-0000-0000000001a2'),
                'membre sans attribution / PA2 : lecture, sans finances');
select tests.ok(jsonb_array_length(bancarisation.mon_contexte()->'appartenances') = 1,
                'membre sans attribution : une appartenance quand même');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000a4');
select tests.ok(jsonb_array_length(bancarisation.mon_contexte()->'appartenances') = 0,
                'membre suspendu : zéro appartenance');
select tests.ok((select count(*) from bancarisation.mes_droits_projets()) = 0,
                'membre suspendu : zéro projet');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000e1');
select tests.ok((select count(*) from bancarisation.mes_droits_projets()) = 1, 'externe : un projet');
select tests.ok((select niveau = 2 and not interne and not finances and not partage
                   from bancarisation.mes_droits_projets()
                  where projet_id = '00000000-0000-0000-0000-0000000001a1'),
                'externe / PA1 : lecture, sans finances');
select tests.ok(jsonb_array_length(bancarisation.mon_contexte()->'appartenances') = 0,
                'externe : zéro appartenance');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000b1');
select tests.ok((select niveau = 4 and interne and finances and partage
                   from bancarisation.mes_droits_projets()
                  where projet_id = '00000000-0000-0000-0000-0000000001b1'),
                'admin B / PB1 : gestion interne');
select tests.ok((select count(*) from bancarisation.mes_droits_projets()
                  where projet_id = '00000000-0000-0000-0000-0000000001a2') = 1,
                'admin B voit PA2 après le transfert');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-0000000000f1');
select tests.ok((select count(*) from bancarisation.mes_droits_projets()) = 0, 'admin plateforme : zéro projet');
select tests.ok(jsonb_array_length(bancarisation.mon_contexte()->'appartenances') = 0,
                'admin plateforme : zéro appartenance');
select tests.ok((bancarisation.mon_contexte()->'profil'->>'admin_plateforme')::boolean,
                'admin plateforme : le profil le dit');
reset role;

-- Montants : tant que occurrence_finance n'existe pas, le trou est connu.
-- Dès 055, un lecteur sans finances voit l'action et des montants null.
do $$
declare
  n int;
  ht numeric;
  colonnes int;
begin
  if to_regclass('bancarisation.occurrence_finance') is null then
    raise notice 'CONNU, corrigé par occurrence_finance : un lecteur sans finances voit encore les montants d''occurrence';
    return;
  end if;

  insert into bancarisation.occurrence_finance (occurrence_id, montant_ht, montant_initial, annee_initiale)
  values ('00000000-0000-0000-0000-0000000002a1', 1000, 800, 2026);

  perform tests.en_tant_que('00000000-0000-0000-0000-0000000000e1');
  execute $q$
    select count(*)
      from bancarisation.occurrence_finance f
      join bancarisation.occurrence o on o.id = f.occurrence_id
     where o.projet_id = '00000000-0000-0000-0000-0000000001a1'
       and (f.montant_ht is not null or f.montant_engage is not null
            or f.montant_realise is not null or f.montant_initial is not null)
  $q$ into n;
  if n > 0 then
    raise exception 'ÉCHEC : externe sans finances voit des lignes occurrence_finance';
  end if;
  raise notice 'ok : externe sans finances : aucune ligne occurrence_finance';

  execute $q$
    select montant_ht from bancarisation.v_occurrence_calendrier
     where id = '00000000-0000-0000-0000-0000000002a1'
  $q$ into ht;
  if ht is not null then
    raise exception 'ÉCHEC : externe lit un montant dans v_occurrence_calendrier';
  end if;
  raise notice 'ok : externe : montant null dans le calendrier';

  perform tests.echoue(
    $q$insert into bancarisation.occurrence_finance (occurrence_id, montant_ht)
       values ('00000000-0000-0000-0000-0000000002a2', 1)$q$,
    'externe ne peut pas écrire occurrence_finance');

  execute 'reset role';
  update bancarisation.membre_organisation set statut = 'actif'
   where utilisateur_id = '00000000-0000-0000-0000-0000000000c1';
  perform tests.en_tant_que('00000000-0000-0000-0000-0000000000c1');
  execute $q$
    select montant_ht from bancarisation.v_occurrence_calendrier
     where id = '00000000-0000-0000-0000-0000000002a1'
  $q$ into ht;
  if ht is not null then
    raise exception 'ÉCHEC : maître d''ouvrage lit un montant dans v_occurrence_calendrier';
  end if;
  raise notice 'ok : maître d''ouvrage : montant null dans le calendrier';

  perform tests.en_tant_que('00000000-0000-0000-0000-0000000000a2');
  execute $q$
    select montant_ht from bancarisation.v_occurrence_calendrier
     where id = '00000000-0000-0000-0000-0000000002a1'
  $q$ into ht;
  if ht is distinct from 1000 then
    raise exception 'ÉCHEC : membre interne ne lit pas le montant (vu %)', ht;
  end if;
  raise notice 'ok : membre interne lit le montant';

  reset role;
  select count(*) into colonnes
    from information_schema.columns
   where table_schema = 'bancarisation'
     and table_name = 'occurrence'
     and column_name like 'montant_%';
  if colonnes = 0 then
    raise notice 'ok : 056 appliquée, occurrence n''a plus de colonne de montant';
  else
    raise notice '056 pas encore appliquée : % colonne(s) de montant encore sur occurrence', colonnes;
  end if;
end $$;
reset role;

grant usage on schema tests to anon;
grant execute on function tests.echoue(text, text) to anon;
select set_config('role', 'anon', true);
select tests.echoue('select bancarisation.mon_contexte()', 'anon refusé sur mon_contexte');
select tests.echoue('select * from bancarisation.mes_droits_projets()', 'anon refusé sur mes_droits_projets');
reset role;

select 'TOUS LES TESTS PASSENT' as resultat,
       (select string_agg(version, ', ' order by version) from public.schema_migrations) as migrations;

rollback;