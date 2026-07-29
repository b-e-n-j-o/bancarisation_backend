-- ============================================================================
-- Migration 024 — Couverture n:n  prescription (arrêté)  ↔  échéance (plan de gestion BE)
-- Schéma : bancarisation   (renumérote selon ta séquence réelle)
-- ----------------------------------------------------------------------------
-- Grain d'accroche = l'ÉCHÉANCE (action détaillée du BE + récurrence).
-- Les occurrences sont atteintes via echeance_id. Le temporel des occurrences
-- est année + mois (annee / mois_debut / mois_fin) : pas de date prévue plate.
-- Règles câblées :
--   * seuls les liens mode='user' comptent comme couverture réelle (les 'ia'
--     restent des propositions, visibles seulement sur l'écran d'appariement) ;
--   * occurrences statut='supprime' exclues des comptes.
-- ============================================================================

-- 1) Table de jonction n:n -----------------------------------------------------
create table if not exists bancarisation.prescription_couverture (
  prescription_id uuid not null
    references bancarisation.arrete_prescription(id) on delete cascade,
  echeance_id     uuid not null
    references bancarisation.echeance(id) on delete cascade,
  mode      text not null default 'ia' check (mode in ('ia','user')),  -- proposé / confirmé
  confiance numeric,   -- score du LLM d'appariement (liens 'ia')
  note      text,      -- justification du LLM ou commentaire humain
  cree_le   timestamptz not null default now(),
  primary key (prescription_id, echeance_id)
);
create index if not exists idx_couv_echeance
  on bancarisation.prescription_couverture(echeance_id);

-- 2) Vue : couverture + exécution par prescription -----------------------------
-- Deux CTE pré-agrégées par prescription → jointure 1:1 en tête, pas de fan-out.
create or replace view bancarisation.v_prescription_couverture as
with liens as (                       -- couverture VALIDÉE uniquement
  select prescription_id, count(distinct echeance_id) as nb_echeances
  from bancarisation.prescription_couverture
  where mode = 'user'
  group by prescription_id
),
occ as (                              -- occurrences atteintes via les échéances liées
  select
    c.prescription_id,
    count(distinct o.id)                                   as nb_occurrences,
    count(distinct o.id) filter (where o.statut = 'realise') as nb_realisees,
    count(distinct o.id) filter (
      where o.statut in ('a_confirmer','planifie','en_cours','repousse')
        and (make_date(o.annee, coalesce(o.mois_fin, 12), 1)
             + interval '1 month' - interval '1 day')::date < current_date
    )                                                      as nb_en_retard,
    min(make_date(o.annee, coalesce(o.mois_debut, 1), 1)) filter (
      where o.statut in ('a_confirmer','planifie','en_cours','repousse')
        and make_date(o.annee, coalesce(o.mois_debut, 1), 1) >= current_date
    )                                                      as prochaine_echeance
  from bancarisation.prescription_couverture c
  join bancarisation.occurrence o
    on o.echeance_id = c.echeance_id
   and o.statut <> 'supprime'
  where c.mode = 'user'
  group by c.prescription_id
)
select
  p.id                            as prescription_id,
  a.projet_id,
  coalesce(l.nb_echeances, 0)     as nb_echeances,
  coalesce(o.nb_occurrences, 0)   as nb_occurrences,
  coalesce(o.nb_realisees, 0)     as nb_realisees,
  coalesce(o.nb_en_retard, 0)     as nb_en_retard,
  o.prochaine_echeance,
  case when coalesce(l.nb_echeances, 0) = 0
       then 'non_couverte' else 'couverte' end as couverture
from bancarisation.arrete_prescription p
join bancarisation.arrete a on a.id = p.arrete_id
left join liens l on l.prescription_id = p.id
left join occ   o on o.prescription_id = p.id;

-- ============================================================================
-- La 3e porte (conformité) se superpose en joignant constat_controle via
-- l'action_fiche de l'échéance (echeance.action_cle → action_fiche.cle),
-- donc via v_action_fiche_conformite — rien à réécrire ici.
-- ============================================================================