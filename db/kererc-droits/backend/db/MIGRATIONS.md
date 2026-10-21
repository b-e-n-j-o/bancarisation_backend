# Migrations KerERC

Remplace `ORDRE.md`.

## Principe

L'historique actuel (`backend/db/0*.sql`, `api/ocr/db/sql/`, `foncier_init.sql`) n'est pas rejouable : numéros en double, scripts V0 que `028` contredit, fichiers cités mais absents (`000_baseline`, `001_referentiels`), et seulement 5 scripts sur 19 qui s'inscrivent dans `schema_migrations`. On ne le répare pas. On le remplace par une **baseline** : une photo de supabase-dev une fois prouvée saine. Tout ce qui suit passe par `outils/appliquer.sh`.

```
backend/db/
  baseline/       photo de la base (générée par outils/figer_baseline.sh, jamais éditée à la main)
  migrations/     067_sujet.sql, 068_sujet.sql… appliquées par appliquer.sh, une seule fois chacune
  archive/        l'ancien historique, pour mémoire : jamais exécuté
  seeds/          comptes et données de recette (jamais en prod)
  tests/          checks_structure.sql, isolation.sql, roles.sql
  outils/         figer_baseline.sh, appliquer.sh
```

## Mise en place (une fois)

1. **Prouver que supabase-dev est saine.** Lancer `tests/checks_structure.sql`, `tests/isolation.sql` et `tests/roles.sql`. Le premier liste aussi les preuves de 028 à 066. Pour 061 et 064 (pas de preuve automatique), vérifier à la main que leurs objets existent.
2. **Corriger les écarts de `roles.sql`.** Une migration par décision, appliquée à l'ancienne une dernière fois, ou un changement d'attendu (dans `roles.ts` et `roles.sql`, même commit). On ne fige pas une base qui fuit.
3. **Figer.** `DATABASE_URL=… backend/db/outils/figer_baseline.sh`. Lire les « ATTENTION » de l'étape 2 : une vue qui lit un autre schéma (`ecocompensation`, par ex.) ne se rejouera pas seule. Relire `baseline/`, compléter `REFERENTIELS` si une table de référence manque.
4. **Prouver que la baseline se rejoue.** En local (`supabase db start`, puis `appliquer.sh` et les trois tests), ou en ouvrant une PR : la CI le fait.
5. **Archiver.** `git mv` de `backend/db/0*.sql`, `ORDRE.md`, `api/ocr/db/sql/` et des `sql/` du foncier vers `backend/db/archive/`. Ils restent lisibles dans le dépôt, plus personne ne les exécute.

## Écrire une migration

- Fichier `migrations/NNN_sujet.sql`, numéro suivant (premier : `067`).
- Pas de `BEGIN`/`COMMIT`, pas d'`INSERT` dans `schema_migrations` : `appliquer.sh` refuse le fichier. Il ouvre la transaction, inscrit la version et recharge PostgREST.
- Une migration appliquée quelque part n'est plus jamais modifiée : on en écrit une autre.
- Si elle touche aux droits, mettre à jour `roles.sql` (et `roles.ts`) dans le même commit.

## Appliquer

```bash
backend/db/outils/appliquer.sh "$DATABASE_URL" --simuler   # ce qui serait appliqué
backend/db/outils/appliquer.sh "$DATABASE_URL"
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f backend/db/tests/checks_structure.sql
```

Même commande en CI, sur supabase-dev et en prod. L'éditeur SQL de Studio sert à explorer, plus à migrer : une modification faite à la main ne laisse pas de trace, et c'est exactement ainsi que l'historique actuel est devenu illisible.

## Quels tests, où

| Test | Écrit en base ? | CI (base vierge) | supabase-dev | Prod |
| --- | --- | --- | --- | --- |
| `checks_structure.sql` | non (READ ONLY) | oui | oui | **oui, après chaque migration** |
| `isolation.sql` | oui, annulé | oui | oui | non |
| `roles.sql` | oui, annulé | oui | oui | non |
| pytest `backend/tests/droits` | via l'API | plus tard (backend + GoTrue) | oui | non |

`isolation.sql` et `roles.sql` insèrent dans `auth.users` avant d'annuler : un trigger qui appelle l'extérieur partirait quand même. Ils ne tournent donc pas sur la prod.

## Si une prod KerERC existe déjà

La baseline vient de supabase-dev. Avant d'utiliser `appliquer.sh` sur la prod, comparer les deux structures :

```bash
diff <(pg_dump "$URL_DEV"  --schema-only -n bancarisation -n prive) \
     <(pg_dump "$URL_PROD" --schema-only -n bancarisation -n prive)
```

Si elles diffèrent, remettre la prod au niveau une dernière fois à l'ancienne. Ensuite seulement, inscrire la version de la baseline dans son `schema_migrations`. À partir de là, dev et prod reçoivent les mêmes fichiers, dans le même ordre, par le même script.
