# Migrations

Deux répertoires partagent aujourd'hui la même numérotation :

- `backend/db/` — socle d'authentification (`027`, `028`, `030`, `030z`, `054`) et `000_isolation.sql` (test, pas une migration).
- `backend/api/ocr/db/sql/` — historique métier, de `001` à `053`.

Les appliquer dans l'ordre des numéros mélange les deux dossiers. Sur une base déjà en service, seul le delta suivant compte : `030z`, puis `054`.

## Reconstruction

Ne pas rejouer `001` à `053` sur une base vide. Ces scripts supposent l'état incomplet du moment où ils ont été écrits (`ALTER` sur des tables déjà là, vues recréées plusieurs fois). Les rejouer ne reconstruit pas le schéma actuel.

Pour une installation neuve :

1. Partir d'un `pg_dump --schema-only` de la base une fois `054` appliqué. Ce dump devient la base, dans `backend/db/`.
2. Ranger `api/ocr/db/sql/` à l'écart (archive, plus dans l'ordre d'application). On le garde pour lire l'historique, on ne l'exécute plus.
3. Les migrations suivantes (finances d'occurrence, etc.) s'ajoutent seulement dans `backend/db/`, après ce dump.

On ne supprime les anciens fichiers qu'après avoir restauré ce dump sur une base vide et revu `000_isolation.sql` dessus.
