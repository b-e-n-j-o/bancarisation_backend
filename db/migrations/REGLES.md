# Règles des migrations (à lire avant d'écrire un fichier ici)

Ce dossier ne contient que des migrations : `NNN_sujet.sql`, numérotées à la suite
(072, 073…). Tout fichier `.sql` placé ici est exécuté par `db/outils/appliquer.sh`
sur toutes les bases (CI, dev, prod). Aucun autre type de fichier SQL ici.

## Format
- Pas de `begin;` / `commit;` / `rollback;` : appliquer.sh ouvre la transaction.
- Pas d'`insert into public.schema_migrations` : appliquer.sh inscrit la version.
- Pas de `notify pgrst` : appliquer.sh recharge PostgREST.
- Une migration appliquée quelque part n'est plus jamais modifiée : on en écrit une nouvelle.
- Jamais lancée à la main dans Studio. Studio sert à regarder.

## Sécurité (sinon la CI est rouge)
- Toute nouvelle table de `bancarisation` : `enable` ET `force row level security`.
- Politiques `to authenticated` uniquement, une par opération : pas de `for all`.
  Seule exception : le worker `kererc_backend` sur `export_projet`, `import_projet`,
  `import_projet_id_map`. Toute nouvelle exception se déclare dans
  `db/tests/checks_structure.sql` et `db/tests/isolation.sql`.
- Pas de `using (true)` hors référentiels.
- Vues : `with (security_invoker = true)`.
- Fonctions `security definer` : `set search_path = ''`, noms entièrement qualifiés,
  `revoke all … from public, anon` puis `grant execute … to authenticated`.
- Jamais de droit pour `anon`.
- Montants : jamais sur une table lisible sans finances. Ils vont dans une table protégée
  par `prive.voit_finances()`.

## Si la migration touche aux droits
Mettre à jour dans le même commit `db/tests/roles.sql` (et `roles.ts` côté frontend).

## Circuit
branche → PR → CI verte → merge → `bash db/outils/appliquer.sh "$DATABASE_URL"`
→ `psql "$DATABASE_URL" -f db/tests/checks_structure.sql`