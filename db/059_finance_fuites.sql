-- 059_finance_fuites.sql
-- Ferme les deux fuites restantes après 055/056 :
--   * montant_ttc et taux_tva quittent occurrence pour occurrence_finance ;
--   * budget_mouvement : une ligne de montant n'est lisible qu'avec voit_finances,
--     une ligne statut / année / mois reste lisible par tout lecteur du projet.
-- Ne pas modifier 056 : il est déjà appliqué et sera rejoué par l'opérateur après déploiement.

begin;

alter table bancarisation.occurrence_finance
  add column if not exists montant_ttc numeric,
  add column if not exists taux_tva numeric;

update bancarisation.occurrence_finance f
   set montant_ttc = o.montant_ttc,
       taux_tva = o.taux_tva
  from bancarisation.occurrence o
 where f.occurrence_id = o.id
   and (o.montant_ttc is not null or o.taux_tva is not null);

insert into bancarisation.occurrence_finance (occurrence_id, montant_ttc, taux_tva)
select o.id, o.montant_ttc, o.taux_tva
  from bancarisation.occurrence o
 where (o.montant_ttc is not null or o.taux_tva is not null)
   and not exists (
     select 1 from bancarisation.occurrence_finance f where f.occurrence_id = o.id
   );

create or replace function bancarisation.log_occurrence_finance()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'INSERT' then
    return new;
  end if;
  if new.montant_ht is distinct from old.montant_ht then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_ht',
            old.montant_ht::text, new.montant_ht::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.montant_engage is distinct from old.montant_engage then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_engage',
            old.montant_engage::text, new.montant_engage::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.montant_realise is distinct from old.montant_realise then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_realise',
            old.montant_realise::text, new.montant_realise::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.montant_ttc is distinct from old.montant_ttc then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_ttc',
            old.montant_ttc::text, new.montant_ttc::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.taux_tva is distinct from old.taux_tva then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'taux_tva',
            old.taux_tva::text, new.taux_tva::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  return new;
end;
$$;

create or replace view bancarisation.v_occurrence_calendrier
with (security_invoker = true) as
select o.id,
    o.projet_id,
    o.echeance_id,
    o.annee,
    o.code,
    o.titre,
    o.categorie,
    o.statut,
    o.ug_ids,
    o.mois_debut,
    o.mois_fin,
    o.traverse_nouvel_an,
    o.origine,
    o.confiance,
    o.champs_a_confirmer,
    o.avertissements,
    o.modifie_le,
    o.date_realisation,
    o.commentaire,
    e.cle as echeance_cle,
    e.code_operation,
    e.libelle as echeance_libelle,
    e.action_cle,
    coalesce(nullif(nullif(o.lib_thema, ''::text), 'autre'::text), nullif(nullif(e.lib_thema, ''::text), 'autre'::text), nullif(af.lib_thema, ''::text), 'autre'::text) as lib_thema,
    f.montant_ht,
    f.montant_ttc,
    f.taux_tva,
    o.prestataire,
    o.ligne_budget_id,
    f.montant_initial,
    f.annee_initiale,
    f.montant_engage,
    f.montant_realise,
    o.prestataire_id,
    coalesce(p.nom, o.prestataire) as prestataire_nom,
    e.recurrence as echeance_recurrence,
    o.date_realisation_fin,
    o.surface_m2,
    o.responsable_id,
    coalesce(nullif(trim(concat_ws(' ', resp.prenom, resp.nom)), ''), resp.nom) as responsable_nom
   from bancarisation.occurrence o
     left join bancarisation.occurrence_finance f on f.occurrence_id = o.id
     left join bancarisation.echeance e on e.id = o.echeance_id
     left join bancarisation.action_fiche af on af.projet_id = o.projet_id and af.cle = e.action_cle
     left join bancarisation.prestataires p on p.id = o.prestataire_id
     left join bancarisation.personnes resp on resp.id = o.responsable_id;

create or replace view bancarisation.v_action_portefeuille
with (security_invoker = true) as
select
  o.id,
  o.projet_id,
  o.echeance_id,
  o.annee,
  o.code,
  o.titre,
  o.categorie,
  o.statut,
  o.ug_ids,
  o.mois_debut,
  o.mois_fin,
  o.traverse_nouvel_an,
  o.origine,
  o.confiance,
  o.champs_a_confirmer,
  o.avertissements,
  o.modifie_le,
  o.date_realisation,
  o.commentaire,
  e.cle as echeance_cle,
  e.code_operation,
  e.libelle as echeance_libelle,
  e.action_cle,
  coalesce(nullif(nullif(o.lib_thema, ''::text), 'autre'::text), nullif(nullif(e.lib_thema, ''::text), 'autre'::text), nullif(af.lib_thema, ''::text), 'autre'::text) as lib_thema,
  f.montant_ht,
  f.montant_ttc,
  f.taux_tva,
  o.prestataire,
  o.ligne_budget_id,
  f.montant_initial,
  f.annee_initiale,
  f.montant_engage,
  f.montant_realise,
  o.prestataire_id,
  coalesce(prst.nom, o.prestataire) as prestataire_nom,
  e.recurrence as echeance_recurrence,
  o.date_realisation_fin,
  o.surface_m2,
  o.responsable_id,
  coalesce(nullif(trim(concat_ws(' ', resp.prenom, resp.nom)), ''), resp.nom) as responsable_nom,
  p.nom               as projet_nom,
  p.reference_interne as projet_reference,
  p.departement       as projet_departement,
  p.organisation_id,
  org.nom             as organisation_nom,
  fen.date_fin_fenetre,
  (o.statut not in ('realise', 'supprime')
   and fen.date_fin_fenetre < current_date) as en_retard,
  et.nb_etapes,
  et.nb_etapes_faites,
  et.etape_courante,
  nt.nb_notes,
  nt.derniere_note_le
from bancarisation.occurrence o
left join bancarisation.occurrence_finance f on f.occurrence_id = o.id
left join bancarisation.echeance e on e.id = o.echeance_id
left join bancarisation.action_fiche af on af.projet_id = o.projet_id and af.cle = e.action_cle
left join bancarisation.prestataires prst on prst.id = o.prestataire_id
left join bancarisation.personnes resp on resp.id = o.responsable_id
left join bancarisation.projets p on p.id = o.projet_id
left join bancarisation.organisations org on org.id = p.organisation_id
cross join lateral (
  select (
    make_date(
      o.annee + case when coalesce(o.traverse_nouvel_an, false) then 1 else 0 end,
      coalesce(o.mois_fin, 12),
      1
    ) + interval '1 month' - interval '1 day'
  )::date as date_fin_fenetre
) fen
left join lateral (
  select
    count(*)::int as nb_etapes,
    count(*) filter (where ae.fait)::int as nb_etapes_faites,
    (select e2.libelle from bancarisation.action_etape e2
      where e2.occurrence_id = o.id and not e2.fait
      order by e2.ordre limit 1) as etape_courante
  from bancarisation.action_etape ae
  where ae.occurrence_id = o.id
) et on true
left join lateral (
  select
    count(*)::int as nb_notes,
    max(n.created_at) as derniere_note_le
  from bancarisation.note_interne n
  where n.occurrence_id = o.id and n.supprime_le is null
) nt on true;


alter table bancarisation.occurrence
  drop column if exists montant_ttc,
  drop column if exists taux_tva;

-- Lecture : statut / année / mois pour tout lecteur ; montants seulement avec voit_finances.
drop policy if exists budget_mouvement_select on bancarisation.budget_mouvement;
create policy budget_mouvement_select on bancarisation.budget_mouvement
  for select to authenticated
  using (
    case
      when champ in ('statut', 'annee', 'mois_debut', 'mois_fin')
        then prive.niveau_projet(projet_id) >= 2
      else prive.voit_finances(projet_id)
    end
  );

-- Écriture du journal : une ligne de calendrier (statut, année, mois) suit le droit
-- d'écrire le projet ; une ligne de montant exige en plus voit_finances.
drop policy if exists budget_mouvement_insert on bancarisation.budget_mouvement;
create policy budget_mouvement_insert on bancarisation.budget_mouvement
  for insert to authenticated
  with check (
    prive.niveau_projet(projet_id) >= 3
    and (
      champ in ('statut', 'annee', 'mois_debut', 'mois_fin')
      or prive.voit_finances(projet_id)
    )
  );

drop policy if exists budget_mouvement_update on bancarisation.budget_mouvement;
create policy budget_mouvement_update on bancarisation.budget_mouvement
  for update to authenticated
  using (
    prive.niveau_projet(projet_id) >= 3
    and (
      champ in ('statut', 'annee', 'mois_debut', 'mois_fin')
      or prive.voit_finances(projet_id)
    )
  )
  with check (
    prive.niveau_projet(projet_id) >= 3
    and (
      champ in ('statut', 'annee', 'mois_debut', 'mois_fin')
      or prive.voit_finances(projet_id)
    )
  );

commit;
