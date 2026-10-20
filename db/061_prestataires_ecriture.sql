-- L'annuaire prestataires était en lecture seule pour authenticated
-- (028 : « écriture backend »). La création depuis la recherche SIREN
-- passe par le JWT utilisateur : un membre actif peut insérer et compléter
-- une fiche. Pas de suppression.

drop policy if exists prestataires_insert on bancarisation.prestataires;
drop policy if exists prestataires_update on bancarisation.prestataires;

create policy prestataires_insert on bancarisation.prestataires
  for insert to authenticated
  with check (exists (select 1 from prive.mes_appartenances()));

create policy prestataires_update on bancarisation.prestataires
  for update to authenticated
  using (exists (select 1 from prive.mes_appartenances()))
  with check (exists (select 1 from prive.mes_appartenances()));
