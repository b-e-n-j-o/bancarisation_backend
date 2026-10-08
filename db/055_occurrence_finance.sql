-- 055_occurrence_finance.sql
-- Sort les montants d'occurrence dans une table protégée par voit_finances.
-- Les colonnes de occurrence restent jusqu'à 056 : ne pas les lire ni les écrire.
--
-- À appliquer dans Studio. Ensuite redéployer le backend : les écritures
-- (PATCH, baseline, ingestion) vont dans occurrence_finance. Sans ce
-- redéploiement, une édition de montant met à jour l'ancienne colonne et
-- n'apparaît plus dans les vues.
-- Pas de partage externe avant 056.
--
-- Recensement des lectures / écritures de montant_ht, montant_engage,
-- montant_realise, montant_initial, annee_initiale (avant cette migration) :
--   Vues : v_occurrence_calendrier (043), v_action_portefeuille (047),
--          v_budget_annuel, v_budget_delta_annuel, v_budget_ecarts (008),
--          v_tresorerie_site (027), v_projet_prestataires (016),
--          v_parc_signal (019, retard + dérive).
--          v_parc_projet, v_parc_financier_annuel lisent v_budget_delta_annuel.
--   Trigger : bancarisation.log_budget_mouvement (029) sur occurrence.
--   Backend : modifier_occurrence, modifier_occurrence_avec_contexte,
--             figer_baseline, ingestion des occurrences, bilan.py, suivi.py,
--             generer_projet_mock.py.
--   Front : calendrier, budget, actions, bilans — ils lisent les vues,
--           pas la table occurrence.

begin;

create table bancarisation.occurrence_finance (
  occurrence_id   uuid primary key references bancarisation.occurrence(id) on delete cascade,
  projet_id       uuid not null references bancarisation.projets(id) on delete cascade,
  montant_ht      numeric,
  montant_engage  numeric,
  montant_realise numeric,
  montant_initial numeric,
  annee_initiale  int,
  modifie_le      timestamptz not null default now()
);

create index occurrence_finance_projet_idx
  on bancarisation.occurrence_finance (projet_id);

comment on table bancarisation.occurrence_finance is
  'Montants d''une occurrence. Visibles seulement si voit_finances. Pas de ligne si aucun montant.';

-- projet_id recopié depuis l'occurrence : la valeur envoyée par un client est écrasée.
create or replace function prive.occurrence_finance_projet()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  select o.projet_id into new.projet_id
    from bancarisation.occurrence o
   where o.id = new.occurrence_id;
  if new.projet_id is null then
    raise exception 'Occurrence % introuvable', new.occurrence_id
      using errcode = '23503';
  end if;
  new.modifie_le := now();
  return new;
end $$;

revoke all on function prive.occurrence_finance_projet() from public, anon;
grant execute on function prive.occurrence_finance_projet() to authenticated;

create trigger occurrence_finance_projet
  before insert or update on bancarisation.occurrence_finance
  for each row execute function prive.occurrence_finance_projet();

insert into bancarisation.occurrence_finance (
  occurrence_id, projet_id,
  montant_ht, montant_engage, montant_realise, montant_initial, annee_initiale
)
select id, projet_id,
       montant_ht, montant_engage, montant_realise, montant_initial, annee_initiale
  from bancarisation.occurrence
 where coalesce(montant_ht, montant_engage, montant_realise, montant_initial) is not null
    or annee_initiale is not null;

alter table bancarisation.occurrence_finance enable row level security;
alter table bancarisation.occurrence_finance force row level security;
revoke all on bancarisation.occurrence_finance from public, anon;

create policy occurrence_finance_lecture on bancarisation.occurrence_finance
  for select to authenticated
  using (prive.voit_finances(projet_id));

create policy occurrence_finance_ajout on bancarisation.occurrence_finance
  for insert to authenticated
  with check (prive.voit_finances(projet_id) and prive.niveau_projet(projet_id) >= 3);

create policy occurrence_finance_modif on bancarisation.occurrence_finance
  for update to authenticated
  using (prive.voit_finances(projet_id) and prive.niveau_projet(projet_id) >= 3)
  with check (prive.voit_finances(projet_id) and prive.niveau_projet(projet_id) >= 3);

create policy occurrence_finance_suppr on bancarisation.occurrence_finance
  for delete to authenticated
  using (prive.voit_finances(projet_id) and prive.niveau_projet(projet_id) >= 3);

grant select, insert, update, delete on bancarisation.occurrence_finance to authenticated;

comment on column bancarisation.occurrence.montant_ht is
  'Obsolète depuis 055. Lue et écrite via occurrence_finance. Supprimée par 056.';
comment on column bancarisation.occurrence.montant_engage is
  'Obsolète depuis 055. Lue et écrite via occurrence_finance. Supprimée par 056.';
comment on column bancarisation.occurrence.montant_realise is
  'Obsolète depuis 055. Lue et écrite via occurrence_finance. Supprimée par 056.';
comment on column bancarisation.occurrence.montant_initial is
  'Obsolète depuis 055. Lue et écrite via occurrence_finance. Supprimée par 056.';
comment on column bancarisation.occurrence.annee_initiale is
  'Obsolète depuis 055. Lue et écrite via occurrence_finance. Supprimée par 056.';

-- ---------------------------------------------------------------------
-- Journal : statut et période restent sur occurrence ; les montants
-- passent sur occurrence_finance. Même set_config que le PATCH.
-- ---------------------------------------------------------------------

create or replace function bancarisation.log_budget_mouvement()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_motif text := current_setting('bancarisation.motif', true);
  v_par   text := current_setting('bancarisation.modifie_par', true);
begin
  if new.statut is distinct from old.statut then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'statut',
            old.statut, new.statut, v_motif, v_par);
  end if;

  if new.annee is distinct from old.annee then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'annee',
            old.annee::text, new.annee::text, v_motif, v_par);
  end if;

  if new.mois_debut is distinct from old.mois_debut then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'mois_debut',
            old.mois_debut::text, new.mois_debut::text, v_motif, v_par);
  end if;

  if new.mois_fin is distinct from old.mois_fin then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'mois_fin',
            old.mois_fin::text, new.mois_fin::text, v_motif, v_par);
  end if;

  return new;
end $$;

comment on function bancarisation.log_budget_mouvement() is
  'Historique append-only occurrence : statut, année, mois_debut, mois_fin. Les montants sont sur occurrence_finance.';

create or replace function bancarisation.log_occurrence_finance()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_motif text := current_setting('bancarisation.motif', true);
  v_par   text := current_setting('bancarisation.modifie_par', true);
begin
  if tg_op = 'INSERT' then
    return new;
  end if;

  if new.montant_ht is distinct from old.montant_ht then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_ht',
            old.montant_ht::text, new.montant_ht::text, v_motif, v_par);
  end if;

  if new.montant_engage is distinct from old.montant_engage then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_engage',
            old.montant_engage::text, new.montant_engage::text, v_motif, v_par);
  end if;

  if new.montant_realise is distinct from old.montant_realise then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_realise',
            old.montant_realise::text, new.montant_realise::text, v_motif, v_par);
  end if;

  return new;
end $$;

revoke all on function bancarisation.log_occurrence_finance() from public, anon;
grant execute on function bancarisation.log_occurrence_finance() to authenticated;

create trigger trg_occurrence_finance_mouvement
  after update on bancarisation.occurrence_finance
  for each row execute function bancarisation.log_occurrence_finance();

-- ---------------------------------------------------------------------
-- Vues. Mêmes colonnes, mêmes types : create or replace suffit.
-- Jointure externe + security invoker : sans voit_finances, montants null.
-- Les sommes ne sont pas coalescées à 0 : un montant caché n'est pas un zéro.
-- ---------------------------------------------------------------------

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
    o.montant_ttc,
    o.taux_tva,
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
  o.montant_ttc,
  o.taux_tva,
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

revoke all on bancarisation.v_occurrence_calendrier from public, anon;
revoke all on bancarisation.v_action_portefeuille from public, anon;
grant select on bancarisation.v_occurrence_calendrier to authenticated;
grant select on bancarisation.v_action_portefeuille to authenticated;

create or replace view bancarisation.v_budget_annuel
with (security_invoker = true) as
select
  o.projet_id,
  o.annee,
  count(*) filter (where f.montant_ht is not null) as nb_lignes_chiffrees,
  count(*)                                          as nb_occurrences,
  sum(f.montant_ht)                                 as total_ht,
  sum(f.montant_realise)                            as total_ht_realise,
  sum(f.montant_engage)                             as total_ht_engage
from bancarisation.occurrence o
left join bancarisation.occurrence_finance f on f.occurrence_id = o.id
where o.statut <> 'supprime'
group by o.projet_id, o.annee;

create or replace view bancarisation.v_budget_delta_annuel
with (security_invoker = true) as
with courant as (
  select o.projet_id, o.annee,
         sum(f.montant_ht)      as prevu,
         sum(f.montant_engage)  as engage,
         sum(f.montant_realise) as realise
  from bancarisation.occurrence o
  left join bancarisation.occurrence_finance f on f.occurrence_id = o.id
  where o.statut <> 'supprime'
  group by o.projet_id, o.annee
),
initial as (
  select o.projet_id, f.annee_initiale as annee,
         sum(f.montant_initial) as initial
  from bancarisation.occurrence o
  join bancarisation.occurrence_finance f on f.occurrence_id = o.id
  where f.montant_initial is not null and f.annee_initiale is not null
  group by o.projet_id, f.annee_initiale
)
select
  coalesce(c.projet_id, i.projet_id) as projet_id,
  coalesce(c.annee, i.annee)         as annee,
  i.initial                          as initial,
  c.prevu                            as prevu,
  c.engage                           as engage,
  c.realise                          as realise,
  c.prevu - i.initial                as delta_prevu_initial
from courant c
full join initial i on i.projet_id = c.projet_id and i.annee = c.annee;

create or replace view bancarisation.v_budget_ecarts
with (security_invoker = true) as
select
  o.projet_id,
  coalesce(f.annee_initiale, o.annee) as annee_ref,
  sum(f.montant_initial) filter (
    where o.statut = 'supprime' and f.montant_initial is not null) as annule,
  sum(f.montant_ht) filter (
    where o.statut <> 'supprime' and f.annee_initiale is not null
      and o.annee <> f.annee_initiale) as glisse_sortant,
  sum(f.montant_ht) filter (
    where o.statut <> 'supprime' and f.montant_initial is null) as ajoute,
  sum(f.montant_ht - f.montant_initial) filter (
    where o.statut <> 'supprime' and f.montant_initial is not null
      and o.annee = f.annee_initiale) as revision_prix
from bancarisation.occurrence o
left join bancarisation.occurrence_finance f on f.occurrence_id = o.id
group by o.projet_id, coalesce(f.annee_initiale, o.annee);

revoke all on bancarisation.v_budget_annuel from public, anon;
revoke all on bancarisation.v_budget_delta_annuel from public, anon;
revoke all on bancarisation.v_budget_ecarts from public, anon;
grant select on bancarisation.v_budget_annuel to authenticated;
grant select on bancarisation.v_budget_delta_annuel to authenticated;
grant select on bancarisation.v_budget_ecarts to authenticated;

create or replace view bancarisation.v_tresorerie_site
with (security_invoker = true) as
with dep as (
  select
    o.projet_id,
    o.annee,
    sum(f.montant_ht) as depenses
  from bancarisation.occurrence o
  left join bancarisation.occurrence_finance f on f.occurrence_id = o.id
  where o.statut <> 'supprime'
  group by o.projet_id, o.annee
),
rec as (
  select
    sa.projet_id,
    v.annee_encaissement as annee,
    sum(v.prix_total_ht) as recettes
  from bancarisation.credit_vente v
  join bancarisation.credit_lot l     on l.id = v.lot_id
  join bancarisation.site_agrement sa on sa.id = l.site_agrement_id
  where v.statut <> 'annule'
    and v.annee_encaissement is not null
  group by sa.projet_id, v.annee_encaissement
)
select
  coalesce(d.projet_id, r.projet_id) as projet_id,
  coalesce(d.annee, r.annee)         as annee,
  d.depenses                         as depenses,
  r.recettes                         as recettes,
  r.recettes - d.depenses            as solde
from dep d
full outer join rec r
  on d.projet_id = r.projet_id and d.annee = r.annee;

revoke all on bancarisation.v_tresorerie_site from public, anon;
grant select on bancarisation.v_tresorerie_site to authenticated;

create or replace view bancarisation.v_projet_prestataires
with (security_invoker = true) as
with depuis_occurrences as (
  select
    o.projet_id,
    o.prestataire_id,
    count(*) filter (where o.statut <> 'supprime') as nb_occurrences,
    count(*) filter (where o.statut = 'realise') as nb_realisees,
    sum(f.montant_ht) filter (where o.statut <> 'supprime') as total_prevu_ht,
    sum(f.montant_engage) filter (where o.statut <> 'supprime') as total_engage_ht,
    sum(f.montant_realise) filter (where o.statut <> 'supprime') as total_realise_ht,
    min(o.annee) filter (where o.statut <> 'supprime') as annee_min,
    max(o.annee) filter (where o.statut <> 'supprime') as annee_max
  from bancarisation.occurrence o
  left join bancarisation.occurrence_finance f on f.occurrence_id = o.id
  where o.prestataire_id is not null
  group by o.projet_id, o.prestataire_id
),
depuis_projet as (
  select
    pp.projet_id,
    pp.prestataire_id,
    pp.role,
    pp.created_at as rattache_le
  from bancarisation.projet_prestataire pp
)
select
  coalesce(occ.projet_id, prj.projet_id) as projet_id,
  coalesce(occ.prestataire_id, prj.prestataire_id) as prestataire_id,
  p.nom as prestataire_nom,
  p.siret,
  p.siret_norm,
  p.forme_juridique,
  p.adresse,
  p.code_postal,
  p.commune,
  p.departement,
  p.email,
  p.telephone,
  p.interlocuteur,
  p.specialites,
  p.categories_mesure,
  p.actif,
  prj.role,
  prj.rattache_le,
  case
    when occ.prestataire_id is not null and prj.prestataire_id is not null then 'les_deux'
    when occ.prestataire_id is not null then 'occurrence'
    else 'projet'
  end as source,
  coalesce(occ.nb_occurrences, 0) as nb_occurrences,
  coalesce(occ.nb_realisees, 0) as nb_realisees,
  occ.total_prevu_ht,
  occ.total_engage_ht,
  occ.total_realise_ht,
  occ.annee_min,
  occ.annee_max
from depuis_occurrences occ
full join depuis_projet prj
  on prj.projet_id = occ.projet_id
 and prj.prestataire_id = occ.prestataire_id
join bancarisation.prestataires p
  on p.id = coalesce(occ.prestataire_id, prj.prestataire_id);

revoke all on bancarisation.v_projet_prestataires from public, anon;
grant select on bancarisation.v_projet_prestataires to authenticated;

-- v_parc_signal : définition de 019, montants lus via occurrence_finance.
create or replace view bancarisation.v_parc_signal
with (security_invoker = true) as
with sig_bilan as (
  select
    o.projet_id,
    'bilan_manquant' as code,
    case when count(*) >= bancarisation.seuil('bilan_manquant_critique')
         then 'critique' else 'attention' end as niveau,
    format(
      '%s bilan(s) annuel(s) non remis : %s.',
      count(*), string_agg(o.annee::text, ', ' order by o.annee)
    ) as libelle,
    count(*)::numeric as valeur,
    jsonb_build_object('annees', jsonb_agg(o.annee order by o.annee)) as detail
  from bancarisation.v_projet_annee_obligation o
  where not exists (
    select 1 from bancarisation.rapport_bilan rb
    where rb.projet_id = o.projet_id
      and rb.annee = o.annee
      and rb.statut = 'valide'
  )
  group by o.projet_id
),
sig_retard as (
  select
    oc.projet_id,
    'retard_execution' as code,
    case when max(extract(year from now())::int - oc.annee)
              >= bancarisation.seuil('retard_critique_ans')
         then 'critique' else 'attention' end as niveau,
    format(
      '%s action(s) d''exercices échus non soldées, la plus ancienne de %s.',
      count(*), min(oc.annee)
    ) as libelle,
    count(*)::numeric as valeur,
    jsonb_build_object(
      'annee_min', min(oc.annee),
      'montant_concerne', sum(f.montant_ht),
      'occurrences', jsonb_agg(oc.id order by oc.annee)
    ) as detail
  from bancarisation.occurrence oc
  left join bancarisation.occurrence_finance f on f.occurrence_id = oc.id
  where oc.statut in ('planifie', 'en_cours', 'a_confirmer')
    and oc.annee < extract(year from now())::int
  group by oc.projet_id
),
sig_sous_conso as (
  select
    d.projet_id,
    'sous_consommation' as code,
    case when sum(d.realise) / nullif(sum(d.prevu), 0)
              < bancarisation.seuil('sous_conso_critique')
         then 'critique' else 'attention' end as niveau,
    format(
      'Seuls %s%% du budget prévu des exercices échus ont été facturés (%s sur %s).',
      round(100 * sum(d.realise) / nullif(sum(d.prevu), 0)),
      round(sum(d.realise)), round(sum(d.prevu))
    ) as libelle,
    round(sum(d.realise) / nullif(sum(d.prevu), 0), 4) as valeur,
    jsonb_build_object(
      'prevu_echu', sum(d.prevu),
      'realise_echu', sum(d.realise),
      'non_consomme', sum(d.prevu) - sum(d.realise)
    ) as detail
  from bancarisation.v_budget_delta_annuel d
  where d.annee < extract(year from now())::int
  group by d.projet_id
  having sum(d.prevu) > 0
     and sum(d.realise) / sum(d.prevu) < bancarisation.seuil('sous_conso_seuil')
),
derive as (
  select
    oc.projet_id,
    sum(f.montant_initial) as initial,
    sum(f.montant_ht) as prevu,
    sum(f.montant_ht) - sum(f.montant_initial) as ecart,
    count(*) filter (
      where abs(coalesce(f.montant_ht, 0) - f.montant_initial) >= 1
        and not exists (
          select 1 from bancarisation.budget_mouvement m
          where m.occurrence_id = oc.id and m.motif is not null
        )
    ) as nb_sans_motif
  from bancarisation.occurrence oc
  join bancarisation.occurrence_finance f on f.occurrence_id = oc.id
  where f.montant_initial is not null
    and oc.statut <> 'supprime'
  group by oc.projet_id
),
sig_derive as (
  select
    d.projet_id,
    'derive_non_justifiee' as code,
    case when abs(d.ecart) / nullif(d.initial, 0)
              >= bancarisation.seuil('derive_critique_pct')
         then 'critique' else 'attention' end as niveau,
    format(
      'Écart de %s%% au budget de référence (%s), dont %s ligne(s) sans motif renseigné.',
      round(100 * d.ecart / nullif(d.initial, 0)),
      round(d.ecart),
      d.nb_sans_motif
    ) as libelle,
    round(abs(d.ecart) / nullif(d.initial, 0), 4) as valeur,
    jsonb_build_object(
      'initial', d.initial, 'prevu', d.prevu, 'ecart', d.ecart,
      'lignes_sans_motif', d.nb_sans_motif
    ) as detail
  from derive d
  where d.initial > 0
    and abs(d.ecart) / d.initial >= bancarisation.seuil('derive_seuil_pct')
    and d.nb_sans_motif > 0
),
activite as (
  select
    p.id as projet_id,
    greatest(
      coalesce((select max(m.modifie_le) from bancarisation.budget_mouvement m
                where m.projet_id = p.id), p.created_at),
      coalesce((select max(rb.genere_le) from bancarisation.rapport_bilan rb
                where rb.projet_id = p.id), p.created_at),
      p.updated_at
    ) as derniere_activite
  from bancarisation.projets p
  where p.statut not in ('archive', 'clos')
),
sig_silence as (
  select
    a.projet_id,
    'silence' as code,
    case when a.derniere_activite
              < now() - make_interval(months => bancarisation.seuil('silence_critique_mois')::int)
         then 'critique' else 'attention' end as niveau,
    format(
      'Aucune activité enregistrée depuis %s mois (dernière le %s).',
      floor(extract(epoch from (now() - a.derniere_activite)) / 2592000)::int,
      to_char(a.derniere_activite, 'DD/MM/YYYY')
    ) as libelle,
    round(extract(epoch from (now() - a.derniere_activite)) / 2592000) as valeur,
    jsonb_build_object('derniere_activite', a.derniere_activite) as detail
  from activite a
  where a.derniere_activite
        < now() - make_interval(months => bancarisation.seuil('silence_mois')::int)
)
select * from sig_bilan
union all select * from sig_retard
union all select * from sig_sous_conso
union all select * from sig_derive
union all select * from sig_silence;

revoke all on bancarisation.v_parc_signal from public, anon;
grant select on bancarisation.v_parc_signal to authenticated;

insert into public.schema_migrations (version) values ('055_occurrence_finance');

commit;

notify pgrst, 'reload schema';
