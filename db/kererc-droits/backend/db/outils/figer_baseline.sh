#!/usr/bin/env bash
# Fige l'état d'une base saine (supabase-dev) en baseline rejouable sur une base vierge.
#
#   DATABASE_URL=postgresql://postgres:...@hote:5432/postgres \
#     backend/db/outils/figer_baseline.sh [version]
#
# Produit backend/db/baseline/ :
#   000_prealable.sql   rôles applicatifs et extensions
#   010_schema.sql      schémas bancarisation et prive (structure, droits, politiques)
#   020_referentiels.sql données des tables de référence
#   030_auth.sql        triggers posés sur auth.users (création du profil)
#   040_migrations.sql  table public.schema_migrations, avec la version de la baseline
#
# La base doit passer checks_structure.sql avant d'être figée : on ne fige pas un trou.
# Relire le diff de baseline/ avant de commiter.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL manquante}"
DB_DIR=$(cd "$(dirname "$0")/.." && pwd)
SORTIE="$DB_DIR/baseline"
VERSION=${1:-baseline_$(date +%Y%m%d)}

# Tables de référence dont les données font partie de la structure (à compléter).
REFERENTIELS=(role_contact_ref ref_geomce_categorie parametre_signal)

PSQL=(psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -q)
mkdir -p "$SORTIE"

echo "1/6 contrôle de structure"
"${PSQL[@]}" -f "$DB_DIR/tests/checks_structure.sql" >/dev/null

echo "2/6 dépendances vers d'autres schémas (à reprendre à la main si la liste n'est pas vide)"
"${PSQL[@]}" -At <<'SQL'
select distinct 'ATTENTION : ' || sn.nspname || '.' || sc.relname || ' dépend de ' || dn.nspname || '.' || dc.relname
  from pg_depend d
  join pg_rewrite rw on rw.oid = d.objid
  join pg_class sc on sc.oid = rw.ev_class
  join pg_namespace sn on sn.oid = sc.relnamespace
  join pg_class dc on dc.oid = d.refobjid
  join pg_namespace dn on dn.oid = dc.relnamespace
 where sn.nspname in ('bancarisation', 'prive')
   and dn.nspname not in ('bancarisation', 'prive', 'pg_catalog', 'information_schema', 'auth', 'extensions', 'public')
union
select distinct 'ATTENTION : clé étrangère ' || n.nspname || '.' || c.conname || ' vers ' || fn.nspname || '.' || fc.relname
  from pg_constraint c
  join pg_namespace n on n.oid = c.connamespace
  join pg_class fc on fc.oid = c.confrelid
  join pg_namespace fn on fn.oid = fc.relnamespace
 where c.contype = 'f' and n.nspname in ('bancarisation', 'prive')
   and fn.nspname not in ('bancarisation', 'prive', 'auth', 'public');
SQL

echo "3/6 préalables (rôles, extensions)"
{
  echo "-- Généré par figer_baseline.sh ($VERSION). Rôles et extensions attendus par le schéma."
  echo "-- Les rôles Supabase (anon, authenticated, service_role…) viennent de l'image."
  "${PSQL[@]}" -At <<'SQL'
select format($f$do $$ begin if not exists (select 1 from pg_roles where rolname = %L) then create role %I nologin noinherit; end if; end $$;$f$,
              r.rolname, r.rolname)
  from pg_roles r
 where r.rolname in ('kererc_backend')
union all
select format('create extension if not exists %I with schema %I;', e.extname, n.nspname)
  from pg_extension e join pg_namespace n on n.oid = e.extnamespace
 where e.extname <> 'plpgsql';
SQL
} > "$SORTIE/000_prealable.sql"

echo "4/6 structure"
pg_dump "$DATABASE_URL" --schema-only --schema=bancarisation --schema=prive \
  --no-publications --no-subscriptions > "$SORTIE/010_schema.sql"

echo "5/6 référentiels"
ARGS=()
for t in "${REFERENTIELS[@]}"; do
  if [[ $("${PSQL[@]}" -Atc "select to_regclass('bancarisation.$t') is not null") == t ]]; then
    ARGS+=(--table="bancarisation.$t")
  else
    echo "  (absente, ignorée : bancarisation.$t)"
  fi
done
if ((${#ARGS[@]})); then
  pg_dump "$DATABASE_URL" --data-only --disable-triggers "${ARGS[@]}" > "$SORTIE/020_referentiels.sql"
else
  echo "-- aucun référentiel" > "$SORTIE/020_referentiels.sql"
fi

echo "6/6 triggers sur auth.users et table des migrations"
{
  echo "-- Triggers posés par KerERC sur auth.users (hors des schémas dumpés)."
  "${PSQL[@]}" -At <<'SQL'
select pg_get_triggerdef(t.oid) || ';'
  from pg_trigger t
 where t.tgrelid = 'auth.users'::regclass and not t.tgisinternal
   and exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                where p.oid = t.tgfoid and n.nspname in ('bancarisation', 'prive'));
SQL
} > "$SORTIE/030_auth.sql"

{
  pg_dump "$DATABASE_URL" --table=public.schema_migrations
  echo "insert into public.schema_migrations (version) values ('$VERSION') on conflict do nothing;"
} > "$SORTIE/040_migrations.sql"

echo
echo "Baseline « $VERSION » écrite dans $SORTIE."
echo "Vérifier qu'elle se rejoue sur une base vierge (CI, ou en local : supabase db start, puis appliquer.sh)."
