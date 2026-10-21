-- L'admin de l'organisation enregistrée sur l'export relit l'archive
-- après que le projet a changé de propriétaire. UUID hexadécimaux uniquement.
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/db/tests/export_transfert.sql
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
  ('06900000-0000-4000-8000-0000000000a1', 'Cedant export', 'bureau_etudes'),
  ('06900000-0000-4000-8000-0000000000b1', 'Cessionnaire export', 'bureau_etudes');

insert into bancarisation.organisation_closure (ancetre_id, descendant_id, profondeur)
select v.a, v.d, v.p
from (values
  ('06900000-0000-4000-8000-0000000000a1'::uuid, '06900000-0000-4000-8000-0000000000a1'::uuid, 0),
  ('06900000-0000-4000-8000-0000000000b1'::uuid, '06900000-0000-4000-8000-0000000000b1'::uuid, 0)
) as v(a, d, p)
where not exists (
  select 1 from bancarisation.organisation_closure c
   where c.ancetre_id = v.a and c.descendant_id = v.d
);

insert into auth.users (id, email, aud, role) values
  ('06900000-0000-4000-8000-0000000000a2', 'cedant.admin@test.local', 'authenticated', 'authenticated'),
  ('06900000-0000-4000-8000-0000000000b2', 'cessionnaire.admin@test.local', 'authenticated', 'authenticated');

insert into bancarisation.membre_organisation
  (utilisateur_id, organisation_id, role, statut, portee) values
  ('06900000-0000-4000-8000-0000000000a2', '06900000-0000-4000-8000-0000000000a1', 'admin', 'actif', 'entite'),
  ('06900000-0000-4000-8000-0000000000b2', '06900000-0000-4000-8000-0000000000b1', 'admin', 'actif', 'entite');

insert into bancarisation.projets (id, organisation_id, nom) values
  ('06900000-0000-4000-8000-0000000000c1', '06900000-0000-4000-8000-0000000000a1', 'Projet transfere');

insert into bancarisation.export_projet
  (id, projet_id, organisation_id, demande_par, statut, storage_path, expire_le)
values
  ('06900000-0000-4000-8000-0000000000d1',
   '06900000-0000-4000-8000-0000000000c1',
   '06900000-0000-4000-8000-0000000000a1',
   '06900000-0000-4000-8000-0000000000a2',
   'termine',
   '06900000-0000-4000-8000-0000000000a1/06900000-0000-4000-8000-0000000000c1/06900000-0000-4000-8000-0000000000d1.zip',
   now() + interval '30 days');

-- Le projet change de propriétaire. Le trigger qui fige l'organisation
-- ne s'applique qu'au rôle authenticated.
update bancarisation.projets
   set organisation_id = '06900000-0000-4000-8000-0000000000b1'
 where id = '06900000-0000-4000-8000-0000000000c1';

select tests.en_tant_que('06900000-0000-4000-8000-0000000000a2');
select tests.ok(
  not bancarisation.peut_exporter_projet('06900000-0000-4000-8000-0000000000c1'),
  'après transfert, l''admin cédant ne peut plus exporter');
select tests.ok(
  (select count(*) from bancarisation.export_projet
    where id = '06900000-0000-4000-8000-0000000000d1') = 1,
  'l''admin cédant relit encore son archive');
reset role;

select tests.en_tant_que('06900000-0000-4000-8000-0000000000b2');
select tests.ok(
  bancarisation.peut_exporter_projet('06900000-0000-4000-8000-0000000000c1'),
  'l''admin cessionnaire peut lancer un nouvel export');
reset role;

rollback;
