-- Retire les comptes @roles.test.
-- Les clés étrangères en cascade enlèvent profils, adhésions et accès projet.
-- L'organisation « [Démo] » et son projet restent : supprimez-les à part si besoin.
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--     -f backend/db/seeds/utilisateurs_roles_test_nettoyage.sql

begin;

delete from auth.users
 where email like '%@roles.test';

commit;
