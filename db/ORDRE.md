# Migrations

Les scripts SQL de supabase-dev s'exécutent dans l'éditeur SQL de l'interface Supabase, par la personne qui a la base. Pas de tunnel SSH pour les appliquer.

`pg_dump` exporte une copie. Il ne vide pas la base et ne la verrouille pas pour les autres. Le baseline et la sauvegarde compressée se font depuis une machine qui joint Postgres ; ils ne passent pas par l'éditeur.

## Ce qui est déjà en service (supabase-dev)

Appliquer seulement le delta, dans cet ordre, après une sauvegarde hors du dépôt :

```bash
pg_dump -Fc --schema=bancarisation --schema=prive \
  "postgresql://…@127.0.0.1:5432/postgres" > sauvegarde_avant_054.dump
```

Ce fichier contient structure et données clients. Il reste hors du repo.

1. Vérifier les doublons `(organisation_id, utilisateur_id)` sur `personnes`. S'il y en a, ne pas appliquer `054`.
2. `030z_vue_action_portefeuille_invoker.sql` — ne pas rejouer `047`.
3. `054_contexte_droits.sql`.
4. `tests/isolation.sql` (transaction annulée, pas une migration).

Ne jamais exécuter `000_baseline.sql` sur supabase-dev ni sur une base qui a déjà le schéma.

## Installation neuve

Ne pas rejouer `001` à `053`. Ces scripts supposent l'état du moment où ils ont été écrits.

1. `000_roles.sql` — crée `kererc_backend` sans mot de passe. Le mot de passe se règle à part.
2. `000_baseline.sql` — structure seule de `bancarisation` et `prive`, propriétaires conservés. Extensions (`postgis`, etc.) ajoutées à la main en tête. `public.schema_migrations` et la ligne `000_baseline` ajoutées à la main en pied : `public` n'est pas dans le dump (objets PostGIS). Les clés étrangères vers `auth.users` restent valides sur tout Supabase.
3. `001_referentiels.sql` — données seules des tables de référence (catégories, rôles de contact, signaux, catalogue Théma, annuaire prestataires, modèles de checklist).
4. À partir de `055` (`occurrence_finance`, puis `056` qui retire les anciennes colonnes). `056` seulement après le redéploiement du backend. Chaque fichier se termine par `insert into public.schema_migrations (version) values ('…');`.

La restauration à blanc se fait sur une base jetable (Postgres local ou `postgres_test`), jamais sur supabase-dev. On y rejoue les tests d'isolation, puis on redump avec la même commande et on diff avec `000_baseline.sql`. Aucune différence attendue.

`api/ocr/db/sql/` sort de l'ordre d'application seulement après ce diff. On garde les fichiers pour lire l'historique.

## Suivi

```sql
create table if not exists public.schema_migrations (
  version text primary key,
  appliquee_le timestamptz default now()
);
```
