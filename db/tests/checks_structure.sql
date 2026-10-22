-- =====================================================================
-- checks_structure.sql — KerERC : contrôles de structure, en lecture seule
--
-- Ne crée rien, n'écrit rien (transaction READ ONLY) : sans danger sur la prod.
-- À lancer après chaque migration, sur chaque base (dev, recette, prod) :
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/db/tests/checks_structure.sql
--
-- Il vérifie ce qu'une dérive de la base réelle peut casser sans qu'aucun test
-- fonctionnel ne le voie : une vieille politique V0 rejouée, un GRANT à anon,
-- une vue recréée sans security_invoker, une table sans RLS forcée.
--
-- Échec : une seule erreur qui liste tout ce qui ne va pas, objet par objet.
-- Succès : le dernier SELECT dit « STRUCTURE CONFORME » et liste les preuves
-- de migrations (informatif, non bloquant).
-- =====================================================================
begin transaction read only;

do $$
declare
  ecarts text[] := '{}';
  l text;
begin
  -- 1. RLS activée ET forcée sur toutes les tables
  select string_agg(c.relname, ', ' order by c.relname) into l
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'bancarisation' and c.relkind in ('r', 'p')
     and not (c.relrowsecurity and c.relforcerowsecurity);
  if l is not null then ecarts := ecarts || ('RLS non activée ou non forcée : ' || l); end if;

  -- 2. Vues en security_invoker (sinon elles lisent avec les droits du propriétaire)
  select string_agg(c.relname, ', ' order by c.relname) into l
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'bancarisation' and c.relkind = 'v'
     and not coalesce(c.reloptions @> array['security_invoker=true'], false);
  if l is not null then ecarts := ecarts || ('vues sans security_invoker : ' || l); end if;

  -- 3. Politiques : rien d'ancien, rien d'ouvert
  select string_agg(tablename || '.' || policyname, ', ' order by tablename, policyname) into l
    from pg_policies
   where schemaname = 'bancarisation'
     and coalesce(qual, '') || coalesce(with_check, '')
         ~ '(peut_lire_projet|peut_ecrire_projet|peut_lire_interne|role_courant|organisation_courante)';
  if l is not null then ecarts := ecarts || ('politiques sur les anciennes fonctions : ' || l); end if;

  select string_agg(tablename || '.' || policyname, ', ' order by tablename, policyname) into l
    from pg_policies
   where schemaname = 'bancarisation'
     and coalesce(qual, '') || coalesce(with_check, '') ilike '%auth.uid() IS NULL%';
  if l is not null then ecarts := ecarts || ('politiques ouvertes sans connexion (auth.uid() IS NULL) : ' || l); end if;

  select string_agg(tablename || '.' || policyname, ', ' order by tablename, policyname) into l
    from pg_policies where schemaname = 'bancarisation' and cmd = 'ALL';
  if l is not null then ecarts := ecarts || ('politiques FOR ALL : ' || l); end if;

  -- Exception unique : le worker d'export (068) écrit le statut avec kererc_backend, sans utilisateur.
  select string_agg(tablename || '.' || policyname || ' ' || roles::text, ', ' order by tablename, policyname) into l
    from pg_policies where schemaname = 'bancarisation' and roles <> array['authenticated']::name[]
     and not (tablename = 'export_projet' and roles = array['kererc_backend']::name[]);
  if l is not null then ecarts := ecarts || ('politiques pas réservées à authenticated : ' || l); end if;

  select string_agg(tablename || '.' || policyname, ', ' order by tablename, policyname) into l
    from pg_policies
   where schemaname = 'bancarisation'
     and (policyname = 'v0_open' or qual = 'true' or with_check = 'true')
     and not (tablename = 'export_projet' and roles = array['kererc_backend']::name[])
     and tablename not in ('ref_geomce_categorie', 'role_contact_ref', 'parametre_signal', 'prestataires',
                           'geomce_communes', 'geomce_lin', 'geomce_pct', 'geomce_surf');
  if l is not null then ecarts := ecarts || ('accès libre hors référentiels : ' || l); end if;

  select string_agg(tablename || '.' || policyname, ', ' order by tablename, policyname) into l
    from pg_policies
   where schemaname = 'bancarisation'
     and tablename in ('budget_baseline', 'budget_import', 'budget_mouvement', 'ligne_budget',
                       'credit_lot', 'credit_palier', 'credit_vente', 'occurrence_finance')
     and coalesce(qual, with_check) not like '%voit_finances%';
  if l is not null then ecarts := ecarts || ('tables financières sans voit_finances : ' || l); end if;

  -- 4. anon : aucun droit sur le schéma métier
  select string_agg(table_name || ' (' || privilege_type || ')', ', ' order by table_name) into l
    from information_schema.role_table_grants
   where grantee in ('anon', 'PUBLIC') and table_schema in ('bancarisation', 'prive');
  if l is not null then ecarts := ecarts || ('tables ou vues accordées à anon / PUBLIC : ' || l); end if;

  -- Une fonction SECURITY DEFINER s'exécute avec les droits de son propriétaire et
  -- traverse la RLS : appelable par anon via /rest/v1/rpc, c'est une porte ouverte.
  -- (Les fonctions de trigger ne sont pas appelables directement : exclues.)
  select string_agg(p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', ', ') into l
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'bancarisation'
     and p.prosecdef
     and p.prorettype <> 'trigger'::regtype
     and has_function_privilege('anon', p.oid, 'EXECUTE')
     and has_schema_privilege('anon', 'bancarisation', 'USAGE');
  if l is not null then ecarts := ecarts || ('fonctions SECURITY DEFINER appelables par anon : ' || l); end if;

  select string_agg(c.relname, ', ') into l
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'bancarisation'
     and case when c.relkind = 'S' then has_sequence_privilege('anon', c.oid, 'USAGE') else false end;
  if l is not null then ecarts := ecarts || ('séquences utilisables par anon : ' || l); end if;

  if has_schema_privilege('anon', 'prive', 'USAGE') then
    ecarts := ecarts || 'anon a USAGE sur le schéma prive'::text;
  end if;

  select string_agg(r.rolname || ' ' || d.defaclobjtype::text || ' ' || d.defaclacl::text, ', ') into l
    from pg_default_acl d
    join pg_namespace n on n.oid = d.defaclnamespace
    join pg_roles r on r.oid = d.defaclrole
   where n.nspname in ('bancarisation', 'prive') and d.defaclacl::text ~ '(^|[{,])anon=';
  if l is not null then ecarts := ecarts || ('privilèges par défaut qui donneront des droits à anon : ' || l); end if;

  -- 5. Vues matérialisées : pas de RLS possible, donc jamais lisibles par anon
  select string_agg(c.relname, ', ') into l
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'bancarisation'
     and case when c.relkind = 'm' then has_table_privilege('anon', c.oid, 'SELECT') else false end;
  if l is not null then ecarts := ecarts || ('vues matérialisées lisibles par anon : ' || l); end if;

  -- 6. Invitations : seulement par le backend (066)
  if to_regclass('bancarisation.invitation') is not null then
    select string_agg(grantee || ' ' || privilege_type, ', ') into l
      from information_schema.role_table_grants
     where table_schema = 'bancarisation' and table_name = 'invitation'
       and grantee in ('anon', 'authenticated', 'PUBLIC');
    if l is not null then ecarts := ecarts || ('invitation accordée à : ' || l); end if;
  end if;

  -- 7. Montants : plus aucune colonne sur occurrence (056, 059)
  select string_agg(column_name, ', ') into l
    from information_schema.columns
   where table_schema = 'bancarisation' and table_name = 'occurrence'
     and (column_name like 'montant%' or column_name = 'taux_tva');
  if l is not null then ecarts := ecarts || ('occurrence expose encore des montants : ' || l); end if;

  if cardinality(ecarts) > 0 then
    raise exception E'ÉCHEC : % problème(s) de structure\n- %',
      cardinality(ecarts), array_to_string(ecarts, E'\n- ');
  end if;
  raise notice 'ok : structure conforme';
end $$;

-- Preuves de migrations (informatif) : ce que la base contient de chaque script
-- qui ne s'inscrit pas dans schema_migrations. « non » ne bloque rien : vérifier
-- à la main si la preuve est la bonne avant de conclure qu'une migration manque.
select 'STRUCTURE CONFORME' as resultat, null::text as migration, null::text as preuve, null::boolean as present
union all
select null, v.migration, v.preuve, v.present
  from (values
    ('028_rls',
     'aucune politique v0_open',
     not exists (select 1 from pg_policies where schemaname = 'bancarisation' and policyname = 'v0_open')),
    ('054_contexte_droits',
     'bancarisation.mon_contexte() existe',
     exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'bancarisation' and p.proname = 'mon_contexte')),
    ('055_occurrence_finance',
     'table occurrence_finance',
     to_regclass('bancarisation.occurrence_finance') is not null),
    ('056_occurrence_drop_montants',
     'occurrence sans colonne montant_*',
     not exists (select 1 from information_schema.columns where table_schema = 'bancarisation'
                  and table_name = 'occurrence' and column_name like 'montant%')),
    ('059_finance_fuites',
     'occurrence_finance.montant_ttc et taux_tva',
     (select count(*) = 2 from information_schema.columns where table_schema = 'bancarisation'
       and table_name = 'occurrence_finance' and column_name in ('montant_ttc', 'taux_tva'))),
    ('060_prestataire_naf_tva',
     'prestataires : une colonne *naf* et une *tva*',
     exists (select 1 from information_schema.columns where table_schema = 'bancarisation'
              and table_name = 'prestataires' and column_name ilike '%naf%')
     and exists (select 1 from information_schema.columns where table_schema = 'bancarisation'
              and table_name = 'prestataires' and column_name ilike '%tva%')),
    ('062_occurrence_responsables',
     'table occurrence_responsable',
     to_regclass('bancarisation.occurrence_responsable') is not null),
    ('063_statut_archive',
     'v_budget_annuel exclut les tâches archivées',
     coalesce(pg_get_viewdef(to_regclass('bancarisation.v_budget_annuel')) ilike '%archive%', false)),
    ('068_export_projet',
     'table export_projet',
     to_regclass('bancarisation.export_projet') is not null),
    ('069_export_transfert',
     'transfert_projet.export_id',
     exists (select 1 from information_schema.columns where table_schema = 'bancarisation'
              and table_name = 'transfert_projet' and column_name = 'export_id')),
    ('070_export_droits_backend',
     'kererc_backend a USAGE sur bancarisation',
     exists (select 1 from pg_roles where rolname = 'kererc_backend')
     and has_schema_privilege('kererc_backend', 'bancarisation', 'USAGE')),
    ('071_roles_matrice',
     'projet_contact.partage_partenaires et contacts_partages()',
     exists (select 1 from information_schema.columns where table_schema = 'bancarisation'
              and table_name = 'projet_contact' and column_name = 'partage_partenaires')
     and exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'bancarisation' and p.proname = 'contacts_partages')),
    ('065_qualite_prestataire',
     'projet_acces.qualite accepte prestataire',
     exists (select 1 from pg_constraint where conname = 'projet_acces_qualite_check'
              and pg_get_constraintdef(oid) ilike '%prestataire%')),
    ('066_hygiene_grants',
     'invitation : RLS forcée, aucun droit pour authenticated',
     coalesce((select c.relforcerowsecurity from pg_class c
                where c.oid = to_regclass('bancarisation.invitation')), false)
     and not exists (select 1 from information_schema.role_table_grants
                      where table_schema = 'bancarisation' and table_name = 'invitation'
                        and grantee = 'authenticated'))
  ) as v(migration, preuve, present);

rollback;