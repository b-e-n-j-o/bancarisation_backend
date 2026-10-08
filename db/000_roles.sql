-- Rôle de connexion du backend. Aucun droit propre : une requête qui
-- oublie SET ROLE authenticated ne voit rien.
--
-- Pas de mot de passe ici. Le régler à part, depuis la variable
-- d'environnement du backend :
--   alter role kererc_backend password '...';

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'kererc_backend') then
    create role kererc_backend login noinherit;
  end if;
end
$$;

grant authenticated to kererc_backend;
