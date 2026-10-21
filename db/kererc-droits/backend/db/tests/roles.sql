-- =====================================================================
-- roles.sql — KerERC : la matrice des rôles rejouée contre la base
--
-- isolation.sql vérifie les frontières entre organisations. Ce script vérifie,
-- DANS un projet, ce que chaque rôle peut lire et écrire, ligne par ligne de
-- « L'essentiel » (frontend : pages/organisation/roles.ts).
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/db/tests/roles.sql
--
-- Transaction annulée à la fin : rien ne persiste. Exécuter avec un rôle qui
-- contourne la RLS (postgres), comme isolation.sql.
--
-- Fonctionnement
--   * Le script ne s'arrête pas au premier écart : il essaie tous les cas pour
--     tous les rôles, puis échoue UNE fois avec la liste complète des écarts.
--   * Chaque écriture est tentée puis annulée (sous-transaction) : les cas
--     suivants voient toujours le même jeu de données.
--   * Attendus : O = autorisé, N = refusé, ~ = toléré (restriction d'écran
--     seulement, ou décision pas encore prise). Un ~ est affiché, jamais bloquant.
--   * Refus reconnus : 42501 (RLS ou droit manquant), P0001 (refus levé par un
--     trigger ou une fonction métier), ou 0 ligne touchée par un UPDATE/DELETE.
--     Toute autre erreur (colonne absente, NOT NULL, clé étrangère…) arrête le
--     script avec « ERREUR DE TEST » : c'est le test qu'il faut corriger.
--   * Succès : le dernier SELECT affiche la matrice obtenue (lisible dans Studio).
--
-- Quand un écart apparaît, deux cas seulement :
--   - la base est trop ouverte ou trop fermée → corriger la politique (migration) ;
--   - la matrice promet autre chose que ce qui est voulu → corriger roles.ts ET
--     la ligne ci-dessous, dans le même commit.
-- =====================================================================
begin;

-- ---------------------------------------------------------------------
-- Outils
-- ---------------------------------------------------------------------
create schema tests;
grant usage on schema tests to authenticated;

create function tests.en_tant_que(p_uid uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',
    json_build_object('sub', p_uid, 'role', 'authenticated', 'aal', 'aal2')::text, true);
  perform set_config('role', 'authenticated', true);
end $$;

-- Lecture : vrai si la requête renvoie au moins une ligne.
create function tests.voit(p_sql text) returns boolean language plpgsql as $$
declare
  n bigint;
begin
  execute format('select count(*) from (%s) t', p_sql) into n;
  return n > 0;
exception
  when insufficient_privilege then
    return false;
  when others then
    raise exception 'ERREUR DE TEST (%) : % — %', sqlstate, sqlerrm, p_sql;
end $$;

-- Écriture : tente la requête, l'annule toujours, et dit si elle a abouti.
create function tests.essai(p_sql text, out autorise boolean, out detail text)
language plpgsql as $$
declare
  n bigint;
begin
  begin
    execute p_sql;
    get diagnostics n = row_count;
    -- Annule l'écriture réussie en levant une erreur maison, attrapée juste en dessous.
    raise exception using errcode = 'TST01', message = n::text;
  exception
    when sqlstate 'TST01' then
      autorise := sqlerrm::bigint > 0;
      detail := case when autorise then null else 'aucune ligne touchée' end;
    when insufficient_privilege then
      autorise := false;
      detail := sqlerrm;
    when raise_exception then
      autorise := false;
      detail := sqlerrm;
  end;
exception
  when others then
    raise exception 'ERREUR DE TEST (%) : % — %', sqlstate, sqlerrm, p_sql;
end $$;

grant execute on all functions in schema tests to authenticated;

-- ---------------------------------------------------------------------
-- Acteurs : un compte par rôle, plus deux témoins qui ne doivent rien voir.
-- L'ordre des acteurs est l'ordre des lettres dans les attendus.
-- niveau / interne / finances : ce que mes_droits_projets() doit renvoyer
-- (null = aucune ligne pour ce projet).
-- ---------------------------------------------------------------------
create table tests.acteur (
  ordre int primary key,
  code text unique not null,
  uid uuid not null,
  niveau int,
  interne boolean,
  finances boolean
);

insert into tests.acteur values
  ( 1, 'admin',        '00000000-0000-0000-0000-00000000d001', 4, true,  true ),
  ( 2, 'gestionnaire', '00000000-0000-0000-0000-00000000d002', 4, true,  true ),
  ( 3, 'contributeur', '00000000-0000-0000-0000-00000000d003', 3, true,  true ),
  ( 4, 'lecteur',      '00000000-0000-0000-0000-00000000d004', 2, true,  true ),
  ( 5, 'operateur',    '00000000-0000-0000-0000-00000000d005', 3, false, true ),
  ( 6, 'instructeur',  '00000000-0000-0000-0000-00000000d006', 2, false, false),
  ( 7, 'partenaire',   '00000000-0000-0000-0000-00000000d007', 2, false, true ),
  ( 8, 'moa',          '00000000-0000-0000-0000-00000000d008', 2, false, false),
  ( 9, 'deposant',     '00000000-0000-0000-0000-00000000d009', 2, false, false),
  (10, 'sans_projet',  '00000000-0000-0000-0000-00000000d00a', null, null, null),
  (11, 'intrus',       '00000000-0000-0000-0000-00000000d00b', null, null, null);

-- ---------------------------------------------------------------------
-- Jeu de données (inséré en postgres, RLS contournée)
-- D : bureau d'études propriétaire du projet P
-- X : organisation des partenaires (aucun n'est interne à P)
-- Y : autre bureau d'études ; son admin est l'intrus
-- ---------------------------------------------------------------------
insert into bancarisation.organisations (id, nom, nature) values
  ('00000000-0000-0000-0000-00000000d0a0', 'Recette rôles D', 'bureau_etudes'),
  ('00000000-0000-0000-0000-00000000d0b0', 'Recette rôles X', 'operateur'),
  ('00000000-0000-0000-0000-00000000d0c0', 'Recette rôles Y', 'bureau_etudes');

insert into auth.users (id, email, aud, role)
select uid, code || '@roles-sql.test', 'authenticated', 'authenticated' from tests.acteur;

insert into bancarisation.membre_organisation (utilisateur_id, organisation_id, role, acces_global, statut)
select a.uid,
       case
         when a.code in ('admin', 'gestionnaire', 'contributeur', 'lecteur', 'sans_projet')
           then '00000000-0000-0000-0000-00000000d0a0'::uuid
         when a.code = 'intrus' then '00000000-0000-0000-0000-00000000d0c0'::uuid
         else '00000000-0000-0000-0000-00000000d0b0'::uuid
       end,
       case when a.code in ('admin', 'intrus') then 'admin' else 'membre' end,
       null,
       'actif'
  from tests.acteur a;

insert into bancarisation.projets (id, organisation_id, nom) values
  ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d0a0', 'Recette rôles P');

-- Accès au projet : mêmes triplets que ROLES[].acces dans roles.ts
insert into bancarisation.projet_acces (projet_id, utilisateur_id, organisation_id, qualite, niveau, voir_finances, expire_le) values
  ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d002', null, null,             'gestionnaire', true,  null),
  ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d003', null, null,             'contributeur', true,  null),
  ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d004', null, null,             'lecteur',      true,  null),
  ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d005', null, 'operateur',      'contributeur', true,  null),
  ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d006', null, 'controle',       'lecteur',      false, null),
  ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d007', null, null,             'lecteur',      true,  null),
  ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d008', null, 'maitre_ouvrage', 'lecteur',      false, null),
  ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d009', null, 'prestataire',    'lecteur',      false, null);

-- Contenu du projet
insert into bancarisation.occurrence (id, projet_id, annee, code, titre, categorie) values
  ('00000000-0000-0000-0000-00000000d2a1', '00000000-0000-0000-0000-00000000d1a1', 2026, 'R-1', 'Action de recette', 'compensation');

insert into bancarisation.occurrence_finance (occurrence_id, montant_ht, montant_initial, annee_initiale) values
  ('00000000-0000-0000-0000-00000000d2a1', 1000, 800, 2026);

insert into bancarisation.budget_mouvement (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val) values
  ('00000000-0000-0000-0000-00000000d2a1', '00000000-0000-0000-0000-00000000d1a1', 'montant_ht', '0', '1000');

insert into bancarisation.action_etape (id, occurrence_id, projet_id, ordre, libelle) values
  ('00000000-0000-0000-0000-00000000d2e1', '00000000-0000-0000-0000-00000000d2a1',
   '00000000-0000-0000-0000-00000000d1a1', 1, 'Étape de recette');

insert into bancarisation.documents (id, projet_id, nom, bucket_path) values
  ('00000000-0000-0000-0000-00000000d3a1', '00000000-0000-0000-0000-00000000d1a1',
   'Pièce de recette', '00000000-0000-0000-0000-00000000d1a1/_projet/recette.pdf');

insert into bancarisation.note_interne (id, projet_id, organisation_id, contenu) values
  ('00000000-0000-0000-0000-00000000d3b1', '00000000-0000-0000-0000-00000000d1a1',
   '00000000-0000-0000-0000-00000000d0a0', 'Note du bureau');

insert into bancarisation.parcelles (id, organisation_id, projet_id, idu, commune, section, numero, contenance_m2, source) values
  ('00000000-0000-0000-0000-00000000d3c1', '00000000-0000-0000-0000-00000000d0a0',
   '00000000-0000-0000-0000-00000000d1a1', '35001000AB0999', 'Rennes', 'AB', '999', 1000, 'recette');

-- À ADAPTER si personnes exige d'autres colonnes (une erreur ici annule tout le script).
-- d3d1 : contact du projet. d3d2 : fiche de l'annuaire du bureau (propriétaire foncier, par ex.),
-- qui n'est contact d'aucun projet.
insert into bancarisation.personnes (id, organisation_id, nom) values
  ('00000000-0000-0000-0000-00000000d3d1', '00000000-0000-0000-0000-00000000d0a0', 'Contact de recette'),
  ('00000000-0000-0000-0000-00000000d3d2', '00000000-0000-0000-0000-00000000d0a0', 'Propriétaire de recette');

insert into bancarisation.projet_contact (id, projet_id, personne_id, role_code)
select '00000000-0000-0000-0000-00000000d3e1', '00000000-0000-0000-0000-00000000d1a1',
       '00000000-0000-0000-0000-00000000d3d1', code
  from bancarisation.role_contact_ref
 order by code
 limit 1;

insert into bancarisation.rapport_bilan (id, projet_id, annee, version, statut, type_bilan) values
  ('00000000-0000-0000-0000-00000000d4a1', '00000000-0000-0000-0000-00000000d1a1', 2026, 1, 'valide',    'ecologique'),
  ('00000000-0000-0000-0000-00000000d4a2', '00000000-0000-0000-0000-00000000d1a1', 2026, 2, 'brouillon', 'ecologique'),
  ('00000000-0000-0000-0000-00000000d4a3', '00000000-0000-0000-0000-00000000d1a1', 2026, 3, 'valide',    'financier');

insert into bancarisation.rapport_suivi (id, projet_id, annee, version, statut, depose_le) values
  ('00000000-0000-0000-0000-00000000d4b1', '00000000-0000-0000-0000-00000000d1a1', 2026, 1, 'depose', now()),
  ('00000000-0000-0000-0000-00000000d4b2', '00000000-0000-0000-0000-00000000d1a1', 2026, 2, 'genere', null);

-- ---------------------------------------------------------------------
-- Cas : une ligne par droit. Attendus dans l'ordre des acteurs :
--
--                 adm ges con lec ope ins par moa dep san int
--                  1   2   3   4   5   6   7   8   9  10  11
-- ---------------------------------------------------------------------
create table tests.cas (
  ordre int primary key,
  cle text unique not null,
  genre text not null check (genre in ('lecture', 'ecriture')),
  attendu text not null,
  requete text not null,
  note text
);

insert into tests.cas (ordre, cle, genre, attendu, requete, note) values
-- Voir le projet, la carte, le calendrier
( 10, 'lire_projet',              'lecture',  'OOOOOOOOONN',
  $q$select 1 from bancarisation.projets where id = '00000000-0000-0000-0000-00000000d1a1'$q$, null),
( 11, 'lire_action',              'lecture',  'OOOOOOOOONN',
  $q$select 1 from bancarisation.occurrence where id = '00000000-0000-0000-0000-00000000d2a1'$q$, null),
( 12, 'lire_etapes',              'lecture',  'OOOOOOOOONN',
  $q$select 1 from bancarisation.action_etape where id = '00000000-0000-0000-0000-00000000d2e1'$q$, null),

-- Voir le budget et les montants
( 20, 'lire_montant',             'lecture',  'OOOOONONNNN',
  $q$select 1 from bancarisation.occurrence_finance where occurrence_id = '00000000-0000-0000-0000-00000000d2a1'$q$, null),
( 21, 'lire_montant_calendrier',  'lecture',  'OOOOONONNNN',
  $q$select 1 from bancarisation.v_occurrence_calendrier
      where id = '00000000-0000-0000-0000-00000000d2a1' and montant_ht is not null$q$, null),
( 22, 'lire_mouvement_montant',   'lecture',  'OOOOONONNNN',
  $q$select 1 from bancarisation.budget_mouvement
      where projet_id = '00000000-0000-0000-0000-00000000d1a1' and champ = 'montant_ht'$q$, null),

-- Voir le dossier, les documents et les échanges
( 30, 'lire_documents',           'lecture',  'OOOOOOO~~NN',
  $q$select 1 from bancarisation.documents where id = '00000000-0000-0000-0000-00000000d3a1'$q$,
  'MOA et déposant : onglet masqué à l''écran seulement (décision du 8 octobre)'),
( 31, 'lire_note_bureau',         'lecture',  'OOOONNNNNNN',
  $q$select 1 from bancarisation.note_interne where id = '00000000-0000-0000-0000-00000000d3b1'$q$, null),

-- Voir le foncier (réservé au bureau)
( 40, 'lire_foncier',             'lecture',  'OOOONNNNNNN',
  $q$select 1 from bancarisation.parcelles where id = '00000000-0000-0000-0000-00000000d3c1'$q$, null),
( 41, 'lire_annuaire',            'lecture',  'OOOONNNNNON',
  $q$select 1 from bancarisation.personnes where id = '00000000-0000-0000-0000-00000000d3d2'$q$,
  'fiche du bureau qui n''est contact d''aucun projet (propriétaire foncier, par ex.)'),

-- Contacts du projet (décision du 8 octobre : visibles des partenaires)
( 50, 'lire_contact_projet',      'lecture',  'OOOOOOOO~NN',
  $q$select 1 from bancarisation.projet_contact where id = '00000000-0000-0000-0000-00000000d3e1'$q$, null),
( 51, 'lire_fiche_contact',       'lecture',  'OOOOOOOO~ON',
  $q$select 1 from bancarisation.personnes where id = '00000000-0000-0000-0000-00000000d3d1'$q$,
  'sans_projet : membre du bureau, lit l''annuaire de son organisation'),

-- Voir les bilans (partenaires : écologiques validés seulement)
( 60, 'lire_bilan_eco_valide',    'lecture',  'OOOOOOOO~NN',
  $q$select 1 from bancarisation.rapport_bilan where id = '00000000-0000-0000-0000-00000000d4a1'$q$, null),
( 61, 'lire_bilan_eco_brouillon', 'lecture',  'OOOO~NNNNNN',
  $q$select 1 from bancarisation.rapport_bilan where id = '00000000-0000-0000-0000-00000000d4a2'$q$,
  'opérateur : à trancher (il modifie les bilans dans la matrice, mais c''est un partenaire)'),
( 62, 'lire_bilan_financier',     'lecture',  'OOOOONONNNN',
  $q$select 1 from bancarisation.rapport_bilan where id = '00000000-0000-0000-0000-00000000d4a3'$q$, null),
( 63, 'lire_rapport_depose',      'lecture',  'OOOOOOOO~NN',
  $q$select 1 from bancarisation.rapport_suivi where id = '00000000-0000-0000-0000-00000000d4b1'$q$, null),
( 64, 'lire_rapport_genere',      'lecture',  'OOOO~NNNNNN',
  $q$select 1 from bancarisation.rapport_suivi where id = '00000000-0000-0000-0000-00000000d4b2'$q$,
  'opérateur : à trancher, comme les bilans en brouillon'),

-- Modifier le projet
( 70, 'modifier_action',          'ecriture', 'OOONONNNNNN',
  $q$update bancarisation.occurrence set titre = titre where id = '00000000-0000-0000-0000-00000000d2a1'$q$, null),
( 71, 'creer_etape',              'ecriture', 'OOONONNNNNN',
  $q$insert into bancarisation.action_etape (occurrence_id, projet_id, ordre, libelle)
      values ('00000000-0000-0000-0000-00000000d2a1', '00000000-0000-0000-0000-00000000d1a1', 99, 'Essai')$q$, null),
( 72, 'modifier_montant',         'ecriture', 'OOONONNNNNN',
  $q$update bancarisation.occurrence_finance set montant_ht = montant_ht
      where occurrence_id = '00000000-0000-0000-0000-00000000d2a1'$q$, null),
( 73, 'deposer_document',         'ecriture', 'OOONONNNNNN',
  $q$insert into bancarisation.documents (projet_id, nom, bucket_path)
      values ('00000000-0000-0000-0000-00000000d1a1', 'Essai', '00000000-0000-0000-0000-00000000d1a1/_projet/essai.pdf')$q$,
  'déposant : N tant que la boîte de réception n''existe pas'),
( 74, 'ecrire_note_bureau',       'ecriture', 'OOONNNNNNNN',
  $q$insert into bancarisation.note_interne (projet_id, organisation_id, contenu)
      values ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d0a0', 'Essai')$q$, null),
( 75, 'modifier_foncier',         'ecriture', 'OOONNNNNNNN',
  $q$update bancarisation.parcelles set commune = commune where id = '00000000-0000-0000-0000-00000000d3c1'$q$, null),
( 76, 'ajouter_contact',          'ecriture', 'OOONNNNNNNN',
  $q$insert into bancarisation.projet_contact (projet_id, personne_id, role_code)
      select '00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d3d2', code
        from bancarisation.role_contact_ref order by code limit 1$q$,
  'édition des contacts : bureau seulement'),

-- Supprimer, figer, valider (gestion)
( 80, 'supprimer_document',       'ecriture', 'OONNNNNNNNN',
  $q$delete from bancarisation.documents where id = '00000000-0000-0000-0000-00000000d3a1'$q$, null),
( 81, 'valider_bilan',            'ecriture', 'OONNNNNNNNN',
  $q$update bancarisation.rapport_bilan set statut = 'valide', valide_le = now()
      where id = '00000000-0000-0000-0000-00000000d4a2'$q$, null),
( 82, 'partager',                 'ecriture', 'O~NNNNNNNNN',
  $q$insert into bancarisation.projet_acces (projet_id, utilisateur_id, niveau)
      values ('00000000-0000-0000-0000-00000000d1a1', '00000000-0000-0000-0000-00000000d00b', 'lecteur')$q$,
  'gestionnaire non admin : à trancher (peut-il ouvrir le projet à quelqu''un ?)'),
( 83, 'supprimer_projet',         'ecriture', 'OONNNNNNNNN',
  $q$delete from bancarisation.projets where id = '00000000-0000-0000-0000-00000000d1a1'$q$, null);

create table tests.resultat (
  acteur text not null,
  cas text not null,
  attendu text not null,
  obtenu boolean not null,
  detail text
);

grant select on tests.acteur, tests.cas to authenticated;
grant insert on tests.resultat to authenticated;

-- Garde-fou : chaque attendu a une lettre par acteur, et seulement O, N ou ~.
do $$
declare
  n int := (select count(*) from tests.acteur);
  l text;
begin
  select string_agg(cle, ', ') into l
    from tests.cas
   where length(attendu) <> n or attendu !~ '^[ON~]+$';
  if l is not null then
    raise exception 'ERREUR DE TEST : attendus mal formés (% lettres O/N/~ attendues) : %', n, l;
  end if;
end $$;

-- ---------------------------------------------------------------------
-- Exécution : chaque acteur, chaque cas
-- ---------------------------------------------------------------------
do $$
declare
  a record;
  c record;
  d record;
  ok boolean;
  det text;
begin
  for a in select * from tests.acteur order by ordre loop
    perform tests.en_tant_que(a.uid);

    -- Ce que le front reçoit (useDroitsProjet) doit correspondre au rôle.
    select m.niveau, m.interne, m.finances into d
      from bancarisation.mes_droits_projets(array['00000000-0000-0000-0000-00000000d1a1']::uuid[]) m;
    insert into tests.resultat (acteur, cas, attendu, obtenu, detail) values (
      a.code, 'droits_front', 'O',
      d.niveau is not distinct from a.niveau
        and d.interne is not distinct from a.interne
        and d.finances is not distinct from a.finances,
      format('mes_droits_projets : niveau %s, interne %s, finances %s ; attendu : niveau %s, interne %s, finances %s',
             coalesce(d.niveau::text, 'aucune ligne'), d.interne, d.finances,
             coalesce(a.niveau::text, 'aucune ligne'), a.interne, a.finances));

    for c in select * from tests.cas order by ordre loop
      if c.genre = 'lecture' then
        ok := tests.voit(c.requete);
        det := null;
      else
        select e.autorise, e.detail into ok, det from tests.essai(c.requete) e;
      end if;
      insert into tests.resultat (acteur, cas, attendu, obtenu, detail)
      values (a.code, c.cle, substr(c.attendu, a.ordre, 1), ok, det);
    end loop;

    execute 'reset role';
  end loop;
end $$;

-- ---------------------------------------------------------------------
-- Verdict : tous les écarts d'un coup
-- ---------------------------------------------------------------------
do $$
declare
  l text;
  n int;
begin
  select count(*),
         string_agg(
           format('%s / %s : attendu %s, obtenu %s%s',
                  r.acteur, r.cas, r.attendu,
                  case when r.obtenu then 'O' else 'N' end,
                  coalesce(' (' || r.detail || ')', '')),
           E'\n' order by coalesce(c.ordre, 0), a.ordre)
    into n, l
    from tests.resultat r
    join tests.acteur a on a.code = r.acteur
    left join tests.cas c on c.cle = r.cas
   where r.attendu in ('O', 'N')
     and r.obtenu is distinct from (r.attendu = 'O');

  if n > 0 then
    raise exception E'ÉCHEC : % écart(s) entre la matrice des rôles et la base\n%', n, l;
  end if;
  raise notice 'ok : % vérifications, aucun écart', (select count(*) from tests.resultat);
end $$;

-- Matrice obtenue, une lettre par acteur dans l'ordre :
-- admin, gestionnaire, contributeur, lecteur, operateur, instructeur,
-- partenaire, moa, deposant, sans_projet, intrus
select 0 as ordre,
       'TOUS LES TESTS PASSENT' as cas,
       null as attendu,
       null as obtenu,
       (select string_agg(version, ', ' order by version) from public.schema_migrations) as note
union all
select c.ordre,
       c.cle,
       c.attendu,
       string_agg(case when r.obtenu then 'O' else 'N' end, '' order by a.ordre),
       c.note
  from tests.cas c
  join tests.resultat r on r.cas = c.cle
  join tests.acteur a on a.code = r.acteur
 group by c.ordre, c.cle, c.attendu, c.note
order by 1;

rollback;
