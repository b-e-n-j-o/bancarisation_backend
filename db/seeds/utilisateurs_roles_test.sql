-- Comptes de recette pour les 8 rôles, sur une base qui n'est pas partagée.
-- Mot de passe : la valeur de VITE_MDP_RECETTE dans frontend/.env.local.
-- Ne pas lancer sur une base utilisée pour de vraies données.
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--     -v mdp_recette="'LE_MOT_DE_PASSE'" \
--     -f backend/db/seeds/utilisateurs_roles_test.sql
--
-- Sans -v projet_id, le script crée l'organisation « [Démo] » et le projet
-- « [Démo] Rôles » (budget, foncier, note, document, bilans brouillon et déposé).
-- Pour rattacher les comptes à un autre projet déjà rempli :
--     -v projet_id="'uuid-du-projet'"
--
-- Adresses : *@roles.test. Le domaine .test n'envoie rien.
-- En npm run dev, le sélecteur de rôle se connecte avec ces comptes.
-- Hors du serveur Vite, le second facteur reste exigé.

\if :{?mdp_recette}
\else
\echo 'Variable manquante : -v mdp_recette="''<mot de passe>''"'
\quit
\endif

\if :{?projet_id}
\else
\set projet_id '\'00000000-0000-4000-8000-0000000000d1\''
\endif

begin;

create extension if not exists pgcrypto schema extensions;

do $$
begin
  if not exists (
    select 1
      from pg_constraint
     where conname = 'projet_acces_qualite_check'
       and pg_get_constraintdef(oid) ilike '%prestataire%'
  ) then
    raise exception
      'projet_acces.qualite n''accepte pas prestataire. Appliquez 065_qualite_prestataire.sql avant ce script.';
  end if;
end $$;

-- Organisation vide : les partenaires passent l'écran d'accueil
-- sans devenir internes du projet testé.
insert into bancarisation.organisations (id, nom, nature, statut)
values (
  '00000000-0000-4000-8000-0000000000ee',
  'Recette partenaires',
  'operateur',
  'active'
)
on conflict (id) do nothing;

-- Projet de démonstration, seulement quand on n'a pas choisi un autre projet.
insert into bancarisation.organisations (id, nom, nature, statut)
select
  '00000000-0000-4000-8000-0000000000d0',
  '[Démo]',
  'bureau_etudes',
  'active'
where :projet_id::uuid = '00000000-0000-4000-8000-0000000000d1'
on conflict (id) do nothing;

insert into bancarisation.projets (id, organisation_id, nom)
select
  '00000000-0000-4000-8000-0000000000d1',
  '00000000-0000-4000-8000-0000000000d0',
  '[Démo] Rôles'
where :projet_id::uuid = '00000000-0000-4000-8000-0000000000d1'
on conflict (id) do nothing;

create temporary table recette_role (
  id uuid primary key,
  email text not null,
  prenom text not null,
  famille text not null,
  niveau text not null,
  voir_finances boolean not null,
  qualite text
) on commit drop;

insert into recette_role (id, email, prenom, famille, niveau, voir_finances, qualite) values
  ('00000000-0000-4000-8000-000000000001', 'gestionnaire@roles.test', 'Gestionnaire', 'bureau', 'gestionnaire', true,  null),
  ('00000000-0000-4000-8000-000000000002', 'contributeur@roles.test',  'Contributeur', 'bureau', 'contributeur', true,  null),
  ('00000000-0000-4000-8000-000000000003', 'lecteur@roles.test',       'Lecteur',      'bureau', 'lecteur',      true,  null),
  ('00000000-0000-4000-8000-000000000004', 'operateur@roles.test',     'Opérateur',    'partenaire', 'contributeur', true,  'operateur'),
  ('00000000-0000-4000-8000-000000000005', 'instructeur@roles.test',   'Instructeur',  'partenaire', 'lecteur',      false, 'controle'),
  ('00000000-0000-4000-8000-000000000006', 'partenaire@roles.test',    'Partenaire',   'partenaire', 'lecteur',      true,  null),
  ('00000000-0000-4000-8000-000000000007', 'moa@roles.test',           'Maître d’ouvrage', 'partenaire', 'lecteur', false, 'maitre_ouvrage'),
  ('00000000-0000-4000-8000-000000000008', 'deposant@roles.test',      'Déposant',     'partenaire', 'lecteur',      false, 'prestataire');

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change_token_new, email_change,
  email_change_token_current, phone_change, phone_change_token, reauthentication_token,
  is_sso_user, is_anonymous
)
select
  '00000000-0000-0000-0000-000000000000',
  r.id,
  'authenticated',
  'authenticated',
  r.email,
  extensions.crypt(:mdp_recette, extensions.gen_salt('bf')),
  now(),
  '{"provider":"email","providers":["email"]}'::jsonb,
  jsonb_build_object('prenom', r.prenom),
  now(),
  now(),
  '', '', '', '', '', '', '', '',
  false,
  false
from recette_role r
on conflict (id) do update
  set email = excluded.email,
      encrypted_password = excluded.encrypted_password,
      email_confirmed_at = coalesce(auth.users.email_confirmed_at, now()),
      updated_at = now();

delete from auth.identities
 where user_id in (select id from recette_role);

insert into auth.identities (
  id, user_id, identity_data, provider, provider_id, last_sign_in_at, created_at, updated_at
)
select
  r.id,
  r.id,
  jsonb_build_object('sub', r.id::text, 'email', r.email),
  'email',
  r.id::text,
  now(),
  now(),
  now()
from recette_role r;

update bancarisation.profils p
   set prenom = r.prenom,
       nom = 'Recette'
  from recette_role r
 where p.utilisateur_id = r.id;

delete from bancarisation.membre_organisation
 where utilisateur_id in (select id from recette_role);

insert into bancarisation.membre_organisation (utilisateur_id, organisation_id, role, acces_global, statut)
select r.id, p.organisation_id, 'membre', null, 'actif'
  from recette_role r
  join bancarisation.projets p on p.id = :projet_id::uuid
 where r.famille = 'bureau';

insert into bancarisation.membre_organisation (utilisateur_id, organisation_id, role, acces_global, statut)
select r.id, '00000000-0000-4000-8000-0000000000ee', 'membre', null, 'actif'
  from recette_role r
 where r.famille = 'partenaire';

delete from bancarisation.projet_acces
 where projet_id = :projet_id::uuid
   and utilisateur_id in (select id from recette_role);

insert into bancarisation.projet_acces (
  projet_id, utilisateur_id, organisation_id, qualite, niveau, voir_finances, expire_le
)
select
  :projet_id::uuid,
  r.id,
  null,
  r.qualite,
  r.niveau,
  r.voir_finances,
  null
from recette_role r;

-- Contenu du projet [Démo], pour qu'un onglet vide ne soit pas confondu avec un onglet masqué.
insert into bancarisation.occurrence (id, projet_id, annee, code, titre, categorie)
select v.id, :projet_id::uuid, v.annee, v.code, v.titre, v.categorie
  from (values
    ('00000000-0000-4000-8000-0000000000a1'::uuid, 2026, 'DEMO-PLANT', 'Plantation de haie', 'compensation'),
    ('00000000-0000-4000-8000-0000000000a2'::uuid, 2026, 'DEMO-SUIVI', 'Suivi écologique', 'compensation')
  ) as v(id, annee, code, titre, categorie)
 where :projet_id::uuid = '00000000-0000-4000-8000-0000000000d1'
on conflict (id) do nothing;

insert into bancarisation.occurrence_finance (
  occurrence_id, projet_id, montant_ht, montant_engage, montant_realise, montant_initial, annee_initiale
)
select
  '00000000-0000-4000-8000-0000000000a1',
  :projet_id::uuid,
  12500,
  8000,
  3200,
  11000,
  2026
where :projet_id::uuid = '00000000-0000-4000-8000-0000000000d1'
on conflict (occurrence_id) do update
  set montant_ht = excluded.montant_ht,
      montant_engage = excluded.montant_engage,
      montant_realise = excluded.montant_realise,
      montant_initial = excluded.montant_initial;

insert into bancarisation.parcelles (
  id, organisation_id, projet_id, idu, commune, section, numero, contenance_m2, source
)
select
  '00000000-0000-4000-8000-0000000000b1',
  '00000000-0000-4000-8000-0000000000d0',
  :projet_id::uuid,
  '35001000AB0123',
  'Rennes',
  'AB',
  '123',
  8420,
  'recette'
where :projet_id::uuid = '00000000-0000-4000-8000-0000000000d1'
on conflict (id) do nothing;

insert into bancarisation.note_interne (id, projet_id, occurrence_id, contenu)
select
  '00000000-0000-4000-8000-0000000000c1',
  :projet_id::uuid,
  '00000000-0000-4000-8000-0000000000a1',
  'Note de recette : visible pour le bureau, masquée pour le maître d’ouvrage et le déposant.'
where :projet_id::uuid = '00000000-0000-4000-8000-0000000000d1'
on conflict (id) do nothing;

insert into bancarisation.documents (
  id, projet_id, nom, nom_fichier, bucket_path, taille_octets, type_mime, categorie
)
select
  '00000000-0000-4000-8000-0000000000e1',
  :projet_id::uuid,
  'Photo de recette',
  'recette.jpg',
  'recette/photo-de-recette.jpg',
  1200,
  'image/jpeg',
  'photos'
where :projet_id::uuid = '00000000-0000-4000-8000-0000000000d1'
on conflict (id) do nothing;

insert into bancarisation.rapport_suivi (
  id, projet_id, annee, version, statut, genere_par, donnees, controles, depose_le
)
select v.id, :projet_id::uuid, 2026, v.version, v.statut,
       '00000000-0000-4000-8000-000000000001',
       '{}'::jsonb, '[]'::jsonb, v.depose_le
  from (values
    ('00000000-0000-4000-8000-0000000000f1'::uuid, 1, 'genere', null::timestamptz),
    ('00000000-0000-4000-8000-0000000000f2'::uuid, 2, 'depose', now())
  ) as v(id, version, statut, depose_le)
 where :projet_id::uuid = '00000000-0000-4000-8000-0000000000d1'
on conflict (id) do nothing;

commit;
