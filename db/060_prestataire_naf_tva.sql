-- Code NAF et n° de TVA intracommunautaire, issus de l'API Recherche Entreprises.
-- Le libellé NAF viendra d'une table de correspondance séparée.

alter table bancarisation.prestataires
  add column if not exists code_naf text null,
  add column if not exists tva text null;

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
