-- Isolation de peut_exporter_projet. Transaction annulée : rien ne persiste.
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/db/tests/export_projet.sql
begin;

create schema tests;
grant usage on schema tests to authenticated;

create function tests.en_tant_que(p_uid uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',
    json_build_object('sub', p_uid, 'role', 'authenticated', 'aal', 'aal2')::text, true);
  perform set_config('request.jwt.claim.sub', p_uid::text, true);
  perform set_config('role', 'authenticated', true);
end $$;

create function tests.ok(p_cond boolean, p_msg text) returns void language plpgsql as $$
begin
  if p_cond is distinct from true then raise exception 'ÉCHEC : %', p_msg; end if;
  raise notice 'ok : %', p_msg;
end $$;

grant execute on all functions in schema tests to authenticated;

insert into bancarisation.organisations (id, nom, nature) values
  ('00000000-0000-0000-0000-00000000e0a9', 'Export parent', 'bureau_etudes'),
  ('00000000-0000-0000-0000-00000000e0a1', 'Export propriétaire', 'bureau_etudes'),
  ('00000000-0000-0000-0000-00000000e0b1', 'Export partenaire', 'bureau_etudes');

insert into bancarisation.organisation_closure (ancetre_id, descendant_id, profondeur)
select v.a, v.d, v.p
from (values
  ('00000000-0000-0000-0000-00000000e0a9'::uuid, '00000000-0000-0000-0000-00000000e0a9'::uuid, 0),
  ('00000000-0000-0000-0000-00000000e0a1'::uuid, '00000000-0000-0000-0000-00000000e0a1'::uuid, 0),
  ('00000000-0000-0000-0000-00000000e0b1'::uuid, '00000000-0000-0000-0000-00000000e0b1'::uuid, 0),
  ('00000000-0000-0000-0000-00000000e0a9'::uuid, '00000000-0000-0000-0000-00000000e0a1'::uuid, 1)
) as v(a, d, p)
where not exists (
  select 1 from bancarisation.organisation_closure c
   where c.ancetre_id = v.a and c.descendant_id = v.d
);

insert into auth.users (id, email, aud, role) values
  ('00000000-0000-0000-0000-00000000e0a1', 'export.admin@test.local', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000e0a2', 'export.membre@test.local', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000e0b1', 'export.partenaire@test.local', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000e0a9', 'export.ancetre@test.local', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000e0f1', 'export.plateforme@test.local', 'authenticated', 'authenticated');

update bancarisation.profils set admin_plateforme = true
 where utilisateur_id = '00000000-0000-0000-0000-00000000e0f1';

insert into bancarisation.membre_organisation
  (utilisateur_id, organisation_id, role, statut, portee) values
  ('00000000-0000-0000-0000-00000000e0a1', '00000000-0000-0000-0000-00000000e0a1', 'admin', 'actif', 'entite'),
  ('00000000-0000-0000-0000-00000000e0a2', '00000000-0000-0000-0000-00000000e0a1', 'membre', 'actif', 'entite'),
  ('00000000-0000-0000-0000-00000000e0b1', '00000000-0000-0000-0000-00000000e0b1', 'admin', 'actif', 'entite'),
  ('00000000-0000-0000-0000-00000000e0a9', '00000000-0000-0000-0000-00000000e0a9', 'admin', 'actif', 'branche');

insert into bancarisation.projets (id, organisation_id, nom) values
  ('00000000-0000-0000-0000-00000000e1a1', '00000000-0000-0000-0000-00000000e0a1', 'Projet export');

insert into bancarisation.projet_acces
  (projet_id, organisation_id, qualite, niveau) values
  ('00000000-0000-0000-0000-00000000e1a1', '00000000-0000-0000-0000-00000000e0b1',
   'bureau_etudes', 'gestionnaire');

insert into bancarisation.note_interne (id, projet_id, organisation_id, contenu) values
  ('00000000-0000-0000-0000-00000000e3b1', '00000000-0000-0000-0000-00000000e1a1',
   '00000000-0000-0000-0000-00000000e0b1', 'Note interne du partenaire');

select tests.en_tant_que('00000000-0000-0000-0000-00000000e0a2');
select tests.ok(
  not bancarisation.peut_exporter_projet('00000000-0000-0000-0000-00000000e1a1'),
  'membre non admin : false');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-00000000e0b1');
select tests.ok(
  not bancarisation.peut_exporter_projet('00000000-0000-0000-0000-00000000e1a1'),
  'admin partenaire (accès latéral) : false');
select tests.ok(
  (select count(*) from bancarisation.note_interne
    where id = '00000000-0000-0000-0000-00000000e3b1') = 1,
  'le partenaire voit sa propre note');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-00000000e0a1');
select tests.ok(
  bancarisation.peut_exporter_projet('00000000-0000-0000-0000-00000000e1a1'),
  'admin propriétaire : true');
select tests.ok(
  (select count(*) from bancarisation.note_interne
    where id = '00000000-0000-0000-0000-00000000e3b1') = 0,
  'admin propriétaire ne voit pas la note interne du partenaire');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-00000000e0a9');
select tests.ok(
  bancarisation.peut_exporter_projet('00000000-0000-0000-0000-00000000e1a1'),
  'admin ancêtre en portée branche : true');
reset role;

select tests.en_tant_que('00000000-0000-0000-0000-00000000e0f1');
select tests.ok(
  not bancarisation.peut_exporter_projet('00000000-0000-0000-0000-00000000e1a1'),
  'admin plateforme sans adhésion : false');
reset role;

rollback;
