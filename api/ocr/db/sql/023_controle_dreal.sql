-- ============================================================================
-- Migration 023 — Couche de contrôle DREAL / service instructeur
-- Schéma : bancarisation
-- ----------------------------------------------------------------------------
-- Principe : on N'AJOUTE rien à occurrence.statut (statut d'EXÉCUTION côté BE).
-- La conformité côté État est une couche séparée. Modèle 3 couches :
--   obligation réglementaire  = arrete_prescription (extrait de l'arrêté)
--   opérations planifiées      = occurrence (existant)
--   constats de suivi          = constat_controle (nouveau)
--
-- Mapping schéma réel (Kerelia) :
--   « mesure » métier DREAL  → bancarisation.action_fiche
--   opérations datées        → bancarisation.occurrence  (singulier)
--   lien occ → fiche         → occurrence.echeance_id
--                              → echeance.action_cle = action_fiche.cle
--   canal d'échange          → bancarisation.demande     (singulier)
--   PDF / pièces             → bancarisation.documents
-- ============================================================================

create extension if not exists pgcrypto;  -- pour gen_random_uuid()

-- ----------------------------------------------------------------------------
-- 1) ARRÊTÉ — ancre juridique. PDF en bucket (via documents), + extraction.
-- ----------------------------------------------------------------------------
create table if not exists bancarisation.arrete (
  id                uuid primary key default gen_random_uuid(),
  projet_id         uuid not null references bancarisation.projets(id) on delete cascade,
  type              text not null
                      check (type in ('declaration_loi_eau','autorisation_env',
                                      'derogation_ep','arrete_modificatif','autre')),
  reference         text,          -- ex. 2023/01/05-004
  autorite          text,          -- ex. DDTM 33 / DREAL NA
  beneficiaire      text,          -- ex. SAS BEOLETTO
  numero_dossier    text,          -- ex. 010007707
  date_signature    date,
  date_notification date,          -- pivot des délais (GéoMCE, recours…)
  document_id       uuid references bancarisation.documents(id),
  extraction        jsonb,         -- sortie brute LLM (traçabilité)
  extraction_modele text,
  confiance         numeric,       -- 0..1
  origine           text not null default 'ia' check (origine in ('ia','user')),
  cree_le           timestamptz not null default now(),
  modifie_le        timestamptz
);
create index if not exists idx_arrete_projet on bancarisation.arrete(projet_id);

-- Une action_fiche peut relever de plusieurs arrêtés (loi eau + dérogation EP).
create table if not exists bancarisation.action_fiche_arrete (
  action_fiche_id uuid not null
    references bancarisation.action_fiche(id) on delete cascade,
  arrete_id       uuid not null
    references bancarisation.arrete(id) on delete cascade,
  primary key (action_fiche_id, arrete_id)
);

-- ----------------------------------------------------------------------------
-- 2) PRESCRIPTIONS extraites = couche "obligation réglementaire".
--    Colonne "prescrit" de l'écran Conformité arrêté ↔ réel.
-- ----------------------------------------------------------------------------
create table if not exists bancarisation.arrete_prescription (
  id               uuid primary key default gen_random_uuid(),
  arrete_id        uuid not null references bancarisation.arrete(id) on delete cascade,
  action_fiche_id  uuid references bancarisation.action_fiche(id) on delete set null,
  article          text,          -- ex. "Article 4"
  intitule         text not null, -- ex. "Surface de compensation à atteindre"
  categorie        text check (categorie in ('compensation','evitement','reduction',
                                             'accompagnement','suivi','chantier','administratif')),
  nature           text check (nature in ('calendaire','recurrente','permanente',
                                          'ponctuelle','seuil')),
  cible_valeur     numeric,       -- ex. 4718
  cible_unite      text,          -- ex. 'm2', '%', 'ha'
  echeance         date,          -- si ponctuelle datable
  recurrence       jsonb,         -- si récurrente
  page_source      int,
  texte_source     text,
  confiance        numeric,
  origine          text not null default 'ia' check (origine in ('ia','user')),
  cree_le          timestamptz not null default now(),
  modifie_le       timestamptz
);
create index if not exists idx_presc_arrete on bancarisation.arrete_prescription(arrete_id);
create index if not exists idx_presc_action on bancarisation.arrete_prescription(action_fiche_id);

-- ----------------------------------------------------------------------------
-- 3) CONSTAT DE CONTRÔLE = couche "constats de suivi".
--    Rattaché à une action_fiche (synthèse) OU à une occurrence (action datée).
--    Au moins l'un des deux.
-- ----------------------------------------------------------------------------
create table if not exists bancarisation.constat_controle (
  id               uuid primary key default gen_random_uuid(),
  action_fiche_id  uuid references bancarisation.action_fiche(id) on delete cascade,
  occurrence_id    uuid references bancarisation.occurrence(id) on delete cascade,
  date_constat     date not null default current_date,
  auteur           text,
  mode             text not null check (mode in ('sur_piece','sur_place')),
  conformite       text not null check (conformite in ('conforme','reserve','non_conforme')),
  commentaire      text,
  documents        jsonb,        -- chemins storage, jamais le binaire
  cree_le          timestamptz not null default now(),
  constraint constat_cible_non_vide
    check (action_fiche_id is not null or occurrence_id is not null)
);
create index if not exists idx_constat_action on bancarisation.constat_controle(action_fiche_id);
create index if not exists idx_constat_occ    on bancarisation.constat_controle(occurrence_id);

-- ----------------------------------------------------------------------------
-- 4) BILAN DE SUIVI réglementaire — workflow instructeur.
--    Distinct de rapport_bilan (bilan financier généré).
-- ----------------------------------------------------------------------------
create table if not exists bancarisation.bilan_suivi (
  id                uuid primary key default gen_random_uuid(),
  projet_id         uuid not null references bancarisation.projets(id) on delete cascade,
  annee             int  not null,
  statut            text not null default 'attendu'
                      check (statut in ('attendu','depose','en_relecture',
                                        'complement_demande','valide','rejete')),
  depose_le         timestamptz,
  statue_le         timestamptz,
  document_id       uuid references bancarisation.documents(id),
  commentaire_dreal text,
  cree_le           timestamptz not null default now(),
  unique (projet_id, annee)
);
create index if not exists idx_bilan_projet on bancarisation.bilan_suivi(projet_id);

-- ----------------------------------------------------------------------------
-- 5) ACTE DE CONTRÔLE — registre des décisions + génération de pièces.
-- ----------------------------------------------------------------------------
create table if not exists bancarisation.acte_dreal (
  id            uuid primary key default gen_random_uuid(),
  projet_id     uuid not null references bancarisation.projets(id) on delete cascade,
  type          text not null check (type in ('validation','demande_complement',
                                              'mise_en_demeure','arrete_modificatif')),
  statut        text not null default 'brouillon'
                  check (statut in ('brouillon','emis','clos')),
  objet         text,
  contenu       jsonb,        -- pré-remplissage depuis les écarts
  delai_reponse date,
  emis_le       timestamptz,
  clos_le       timestamptz,
  document_id   uuid references bancarisation.documents(id),
  demande_id    uuid references bancarisation.demande(id),
  cree_le       timestamptz not null default now()
);
create index if not exists idx_acte_projet on bancarisation.acte_dreal(projet_id);
create index if not exists idx_acte_statut on bancarisation.acte_dreal(statut);

-- ----------------------------------------------------------------------------
-- 6) OVERRIDE de statut de contrôle sur le projet (ex. clôture manuelle).
--    Statut effectif = coalesce(override, statut dérivé).
-- ----------------------------------------------------------------------------
alter table bancarisation.projets
  add column if not exists statut_controle_override text
    check (statut_controle_override in ('en_suivi','a_instruire','complement_attente',
                                        'non_conforme','mise_en_demeure','clos'));

-- ============================================================================
-- VUES
-- ============================================================================

-- Helper : occurrence → action_fiche via échéance.action_cle
-- (pas de colonne occurrence.mesure_id dans ce schéma)
create or replace view bancarisation.v_occurrence_action_fiche as
select
  o.id              as occurrence_id,
  o.projet_id,
  o.echeance_id,
  o.annee,
  o.statut          as statut_execution,
  o.date_realisation,
  af.id             as action_fiche_id,
  af.code           as action_code,
  af.titre          as action_titre,
  af.categorie      as action_categorie
from bancarisation.occurrence o
left join bancarisation.echeance e
  on e.id = o.echeance_id
left join bancarisation.action_fiche af
  on af.projet_id = o.projet_id
 and af.cle = e.action_cle;

-- Conformité par action_fiche : dernier constat (direct OU via occurrence) fait foi.
create or replace view bancarisation.v_action_fiche_conformite as
with constats_direct as (
  select action_fiche_id, conformite, date_constat
  from bancarisation.constat_controle
  where action_fiche_id is not null
),
constats_occ as (
  select v.action_fiche_id, c.conformite, c.date_constat
  from bancarisation.constat_controle c
  join bancarisation.v_occurrence_action_fiche v on v.occurrence_id = c.occurrence_id
  where c.occurrence_id is not null
    and v.action_fiche_id is not null
),
tous as (
  select * from constats_direct
  union all
  select * from constats_occ
),
dernier as (
  select distinct on (action_fiche_id)
    action_fiche_id, conformite, date_constat
  from tous
  order by action_fiche_id, date_constat desc
),
nb_cst as (
  select action_fiche_id, count(*)::int as nb_constats
  from tous
  group by action_fiche_id
)
select
  af.id                              as action_fiche_id,
  af.projet_id,
  af.code,
  af.titre,
  af.categorie,
  coalesce(d.conformite, 'non_evalue') as conformite,
  d.date_constat                     as dernier_constat_le,
  coalesce(n.nb_constats, 0)         as nb_constats,
  count(p.id)::int                   as nb_prescriptions
from bancarisation.action_fiche af
left join dernier d on d.action_fiche_id = af.id
left join nb_cst n  on n.action_fiche_id = af.id
left join bancarisation.arrete_prescription p on p.action_fiche_id = af.id
group by
  af.id, af.projet_id, af.code, af.titre, af.categorie,
  d.conformite, d.date_constat, n.nb_constats;

-- Alias de compat (si l'API / docs parlent encore de « mesure »)
create or replace view bancarisation.v_mesure_conformite as
select
  action_fiche_id as mesure_id,
  projet_id,
  code,
  titre,
  categorie,
  conformite,
  dernier_constat_le,
  nb_constats,
  nb_prescriptions
from bancarisation.v_action_fiche_conformite;

-- Statut de contrôle DÉRIVÉ au niveau projet (priorité descendante).
create or replace view bancarisation.v_projet_statut_controle as
select
  pr.id as projet_id,
  case
    when exists (
      select 1 from bancarisation.acte_dreal a
      where a.projet_id = pr.id
        and a.type = 'mise_en_demeure' and a.statut = 'emis'
    ) then 'mise_en_demeure'
    when exists (
      select 1 from bancarisation.v_action_fiche_conformite c
      where c.projet_id = pr.id and c.conformite = 'non_conforme'
    ) then 'non_conforme'
    when exists (
      select 1 from bancarisation.acte_dreal a
      where a.projet_id = pr.id
        and a.type = 'demande_complement' and a.statut = 'emis'
    ) then 'complement_attente'
    when exists (
      select 1 from bancarisation.bilan_suivi b
      where b.projet_id = pr.id
        and b.statut in ('depose','en_relecture')
    ) then 'a_instruire'
    else 'en_suivi'
  end as statut_controle
from bancarisation.projets pr;

-- Statut EFFECTIF = override manuel s'il existe, sinon dérivé.
create or replace view bancarisation.v_projet_statut_effectif as
select
  p.id as projet_id,
  coalesce(p.statut_controle_override, v.statut_controle) as statut_controle,
  (p.statut_controle_override is not null) as force_manuel
from bancarisation.projets p
join bancarisation.v_projet_statut_controle v on v.projet_id = p.id;

-- Bannette « à traiter » — décisions à trancher (alimente l'onglet L1).
create or replace view bancarisation.v_bannette_a_traiter as
select
  'bilan:' || b.id::text                    as id,
  b.projet_id,
  p.nom                                     as projet_nom,
  o.nom                                     as organisation_nom,
  'bilan_a_valider'::text                   as motif,
  ('Bilan ' || b.annee || ' — ' || b.statut) as libelle,
  null::date                                as echeance,
  2                                         as priorite,
  se.statut_controle,
  b.id                                      as bilan_id,
  null::uuid                                as acte_id
from bancarisation.bilan_suivi b
join bancarisation.projets p on p.id = b.projet_id
join bancarisation.organisations o on o.id = p.organisation_id
join bancarisation.v_projet_statut_effectif se on se.projet_id = b.projet_id
where b.statut in ('depose', 'en_relecture')

union all

select
  'acte:' || a.id::text,
  a.projet_id,
  p.nom,
  o.nom,
  case
    when a.statut = 'brouillon' then 'acte_brouillon'
    when a.type = 'mise_en_demeure' then 'mise_en_demeure_suivi'
    when a.type = 'demande_complement' then 'complement_delai'
    else 'a_instruire'
  end,
  coalesce(a.objet, a.type),
  a.delai_reponse,
  case
    when a.type = 'mise_en_demeure' then 3
    when a.type = 'demande_complement' and a.statut = 'emis' then 2
    else 1
  end,
  se.statut_controle,
  null::uuid,
  a.id
from bancarisation.acte_dreal a
join bancarisation.projets p on p.id = a.projet_id
join bancarisation.organisations o on o.id = p.organisation_id
join bancarisation.v_projet_statut_effectif se on se.projet_id = a.projet_id
where a.statut in ('brouillon', 'emis')
  and a.type in ('demande_complement', 'mise_en_demeure', 'validation');

-- ============================================================================
-- Fin migration 023
-- ============================================================================
