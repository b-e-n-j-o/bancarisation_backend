-- 063_statut_archive.sql — appliquée dans Studio le 22 octobre 2026 (transaction et inscription incluses).
begin;

-- Le statut « archive » retire une tâche du calendrier sans la détruire.
-- Les vues budgétaires déjà déployées ne filtraient que « supprime » :
-- une tâche archivée (ou issue d'une fusion) continuerait à compter.
-- On recrée les vues pour exclure aussi « archive ».

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
where o.statut not in ('supprime', 'archive')
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
  where o.statut not in ('supprime', 'archive')
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
    where o.statut in ('supprime', 'archive') and f.montant_initial is not null) as annule,
  sum(f.montant_ht) filter (
    where o.statut not in ('supprime', 'archive') and f.annee_initiale is not null
      and o.annee <> f.annee_initiale) as glisse_sortant,
  sum(f.montant_ht) filter (
    where o.statut not in ('supprime', 'archive') and f.montant_initial is null) as ajoute,
  sum(f.montant_ht - f.montant_initial) filter (
    where o.statut not in ('supprime', 'archive') and f.montant_initial is not null
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
  where o.statut not in ('supprime', 'archive')
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
    count(*) filter (where o.statut not in ('supprime', 'archive')) as nb_occurrences,
    count(*) filter (where o.statut = 'realise') as nb_realisees,
    sum(f.montant_ht) filter (where o.statut not in ('supprime', 'archive')) as total_prevu_ht,
    sum(f.montant_engage) filter (where o.statut not in ('supprime', 'archive')) as total_engage_ht,
    sum(f.montant_realise) filter (where o.statut not in ('supprime', 'archive')) as total_realise_ht,
    min(o.annee) filter (where o.statut not in ('supprime', 'archive')) as annee_min,
    max(o.annee) filter (where o.statut not in ('supprime', 'archive')) as annee_max
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
  occ.annee_max,
  p.code_naf,
  p.tva
from depuis_occurrences occ
full join depuis_projet prj
  on prj.projet_id = occ.projet_id
 and prj.prestataire_id = occ.prestataire_id
join bancarisation.prestataires p
  on p.id = coalesce(occ.prestataire_id, prj.prestataire_id);

revoke all on bancarisation.v_projet_prestataires from public, anon;
grant select on bancarisation.v_projet_prestataires to authenticated;

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
    and oc.statut not in ('supprime', 'archive')
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


insert into public.schema_migrations (version) values ('063_statut_archive');
commit;
notify pgrst, 'reload schema';