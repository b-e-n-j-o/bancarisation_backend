-- Rôle de connexion du backend FastAPI : aucun droit propre.
-- Une requête qui oublie SET ROLE authenticated ne voit rien.
-- Remplacer <secret> avant exécution.

create role kererc_backend login password '6GmBbDEoZOQY/m9kID8I/okNnwzQ0hgftittI5W34Fk=' noinherit;
grant authenticated to kererc_backend;
grant usage on schema bancarisation to authenticated;
grant usage on schema prive to authenticated;
