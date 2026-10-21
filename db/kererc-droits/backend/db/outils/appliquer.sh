#!/usr/bin/env bash
# Seule façon d'appliquer une migration, sur toutes les bases (CI, dev, prod).
#
#   backend/db/outils/appliquer.sh "postgresql://..."      # ou DATABASE_URL dans l'environnement
#   backend/db/outils/appliquer.sh "postgresql://..." --simuler   # liste sans rien appliquer
#
# Base vierge (pas de public.schema_migrations) : applique baseline/*.sql.
# Puis applique, dans l'ordre, chaque fichier de migrations/ absent de schema_migrations :
# le fichier, son inscription et le rechargement de PostgREST dans UNE transaction.
# Un échec annule le fichier en cours et arrête tout : rien n'est appliqué à moitié.
#
# Règles d'écriture d'une migration (migrations/NNN_sujet.sql, à partir de 067) :
#   - pas de BEGIN ni de COMMIT (le script s'en charge) ;
#   - pas d'INSERT dans schema_migrations (le script s'en charge) ;
#   - jamais modifiée une fois appliquée quelque part : on en écrit une nouvelle.
set -euo pipefail

URL=${1:-${DATABASE_URL:?"donner l'URL de la base en argument ou dans DATABASE_URL"}}
SIMULER=${2:-}
DB_DIR=$(cd "$(dirname "$0")/.." && pwd)
PSQL=(psql "$URL" -X -q -v ON_ERROR_STOP=1)

vierge=$("${PSQL[@]}" -Atc "select to_regclass('public.schema_migrations') is null")
if [[ $vierge == t ]]; then
  echo "Base vierge : baseline"
  for f in "$DB_DIR"/baseline/*.sql; do
    echo "  $(basename "$f")"
    [[ $SIMULER == --simuler ]] || "${PSQL[@]}" -1 -f "$f" >/dev/null
  done
fi

n=0
for f in $(find "$DB_DIR/migrations" -maxdepth 1 -name '*.sql' | sort); do
  v=$(basename "$f" .sql)
  if [[ $vierge == f || $SIMULER != --simuler ]]; then
    deja=$("${PSQL[@]}" -Atc "select count(*) from public.schema_migrations where version = '$v'" 2>/dev/null || echo 0)
    [[ $deja == 1 ]] && continue
  fi
  if grep -qiE '^[[:space:]]*(begin|commit|rollback)[[:space:]]*;' "$f"; then
    echo "REFUSÉ : $v contient BEGIN/COMMIT. Le script ouvre lui-même la transaction." >&2
    exit 1
  fi
  if grep -qi 'schema_migrations' "$f"; then
    echo "REFUSÉ : $v écrit dans schema_migrations. Le script l'inscrit lui-même." >&2
    exit 1
  fi
  echo "Migration $v"
  n=$((n + 1))
  [[ $SIMULER == --simuler ]] && continue
  "${PSQL[@]}" -1 -f "$f" \
    -c "insert into public.schema_migrations (version) values ('$v')" \
    -c "notify pgrst, 'reload schema'"
done

if [[ $SIMULER == --simuler ]]; then
  echo "Simulation : $n migration(s) seraient appliquées."
else
  echo "$n migration(s) appliquée(s). Lancer ensuite tests/checks_structure.sql sur cette base."
fi
