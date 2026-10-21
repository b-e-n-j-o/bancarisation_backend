-- Trois comptes témoins, à lancer APRÈS utilisateurs_roles_test.sql, sur la même base.
-- Ils servent aux tests pytest des routes (backend/tests/droits) et à la recette manuelle :
--   admin@roles.test        admin de l'organisation du projet de recette (gère les accès)
--   sans-projet@roles.test  membre de cette organisation, sans aucun accès projet : ne doit rien voir
--   intrus@roles.test       admin d'une autre organisation : ne doit rien voir ni rien faire sur le projet
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--     -v mdp_recette="$(grep '^VITE_MDP_RECETTE=' frontend/.env.local | cut -d= -f2- | tr -d '\r\"')" \
--     -f backend/db/seeds/utilisateurs_temoins_test.sql
--
-- Optionnel : -v projet_id=<uuid> (par défaut, le projet « [Démo] Rôles »).
-- Le nettoyage (utilisateurs_roles_test_nettoyage.sql) les retire aussi (même domaine).

\if :{?mdp_recette}
\else
\echo 'Variable manquante : -v mdp_recette=<mot de passe>'
\quit
\endif

\if :{?projet_id}
\else
\set projet_id 00000000-0000-4000-8000-0000000000d1
\endif

select (count(*) = 1) as projet_existe
  from bancarisation.projets where id = :'projet_id'::uuid \gset
\if :projet_existe
\else
\echo 'Projet de recette introuvable : lancez d''abord utilisateurs_roles_test.sql'
\quit
\endif

begin;

insert into bancarisation.organisations (id, nom, nature, statut)
values ('00000000-0000-4000-8000-0000000000ef', 'Recette intrus', 'bureau_etudes', 'active')
on conflict (id) do nothing;

create temporary table recette_temoin (
  id uuid primary key,
  email text not null,
  prenom text not null,
  organisation_id uuid not null,
  role text not null
) on commit drop;

insert into recette_temoin
select '00000000-0000-4000-8000-000000000009'::uuid, 'admin@roles.test', 'Admin', p.organisation_id, 'admin'
  from bancarisation.projets p where p.id = :'projet_id'::uuid
union all
select '00000000-0000-4000-8000-00000000000a'::uuid, 'sans-projet@roles.test', 'Sans projet', p.organisation_id, 'membre'
  from bancarisation.projets p where p.id = :'projet_id'::uuid
union all
select '00000000-0000-4000-8000-00000000000b'::uuid, 'intrus@roles.test', 'Intrus',
       '00000000-0000-4000-8000-0000000000ef'::uuid, 'admin';

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change_token_new, email_change,
  email_change_token_current, phone_change, phone_change_token, reauthentication_token,
  is_sso_user, is_anonymous
)
select '00000000-0000-0000-0000-000000000000', t.id, 'authenticated', 'authenticated', t.email,
       extensions.crypt(:'mdp_recette', extensions.gen_salt('bf')), now(),
       '{"provider":"email","providers":["email"]}'::jsonb, jsonb_build_object('prenom', t.prenom),
       now(), now(), '', '', '', '', '', '', '', '', false, false
  from recette_temoin t
on conflict (id) do update
  set email = excluded.email,
      encrypted_password = excluded.encrypted_password,
      email_confirmed_at = coalesce(auth.users.email_confirmed_at, now()),
      updated_at = now();

delete from auth.identities where user_id in (select id from recette_temoin);
insert into auth.identities (id, user_id, identity_data, provider, provider_id, last_sign_in_at, created_at, updated_at)
select t.id, t.id, jsonb_build_object('sub', t.id::text, 'email', t.email), 'email', t.id::text, now(), now(), now()
  from recette_temoin t;

update bancarisation.profils p
   set prenom = t.prenom, nom = 'Recette'
  from recette_temoin t
 where p.utilisateur_id = t.id;

delete from bancarisation.membre_organisation where utilisateur_id in (select id from recette_temoin);
insert into bancarisation.membre_organisation (utilisateur_id, organisation_id, role, acces_global, statut)
select t.id, t.organisation_id, t.role, null, 'actif' from recette_temoin t;

-- Aucun accès projet pour les témoins : l'admin voit le projet par son rôle d'organisation.
delete from bancarisation.projet_acces where utilisateur_id in (select id from recette_temoin);

commit;
