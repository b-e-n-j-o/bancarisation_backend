-- Année calendaire de N pour toutes les règles du projet.
-- Ce n'est pas la première année d'une échéance.

alter table bancarisation.projets
  add column annee_n integer;

comment on column bancarisation.projets.annee_n is
  'Année calendaire de N, commune aux règles de récurrence du projet.';

update bancarisation.projets
set annee_n = 2025
where id = '8d061654-c30c-4ced-95a6-99297b43020a';
