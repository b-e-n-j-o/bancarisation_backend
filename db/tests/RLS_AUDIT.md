# Audit RLS — clé anon et PostgREST

Date : 20 octobre 2026. Périmètre : code du dépôt uniquement. La base `https://supabase-dev.kerelia.fr` n'a pas été interrogée. Aucune policy ni migration n'est proposée ici.

Le front embarque la clé **anon** (`VITE_SUPABASE_ANON_KEY`, JWT de rôle `anon`) et interroge PostgREST sur le schéma `bancarisation`. Sans RLS, ou avec une policy permissive qui s'ajoute aux autres, cette clé lit et écrit les tables sur lesquelles `anon` (ou `PUBLIC`) a un `GRANT`.

## 1. Clé utilisée

Client unique : `frontend/src/lib/supabase.ts`.

- URL : `VITE_SUPABASE_URL`
- Clé : `VITE_SUPABASE_ANON_KEY`
- Schéma PostgREST par défaut : `bancarisation` (`db: { schema: 'bancarisation' }`)
- Session persistée, donc un utilisateur connecté envoie un JWT de rôle `authenticated`. Un appel sans session reste en `anon`.

Variables `VITE_*` vues dans le front : `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_BASE_URL`, `VITE_CONTROLE_MOCK`.

**Alerte `service_role` : non.** Aucune occurrence de `service_role` / `SERVICE_ROLE` dans `frontend/` (sources et `.env.local`). La clé n'est pas dans une variable `VITE_*`.

La `service_role` est lue uniquement par le backend (`backend/api/db/supabase.py`, variables `SERVICE_ROLE_KEY` puis `SUPABASE_SERVICE_ROLE_KEY` dans `backend/.env`). Ce client contourne la RLS. Il sert au Storage et à Auth Admin. Le rapport ne recopie aucune clé.

## 2. Appels supabase-js du front

Pas de `.storage.from`, pas de Realtime (`channel` / `postgres_changes`). Tous les `.from` / `.rpc` ci-dessous visent le schéma `bancarisation` (explicite, ou défaut du client via `banc()` / `supabase`).

Auth GoTrue (pas des tables PostgREST) : `signInWithPassword`, `signOut`, `getSession`, `onAuthStateChange`, `resetPasswordForEmail`, `verifyOtp`, `updateUser`, `mfa.listFactors`, `mfa.unenroll`, `mfa.enroll`, `mfa.challengeAndVerify`, `mfa.getAuthenticatorAssuranceLevel`. Fichiers : `frontend/src/lib/api.ts`, `frontend/src/auth/AuthProvider.tsx`, `frontend/src/pages/auth/*`, `frontend/src/pages/compte/ComptePage.tsx`.

### 2.1 Inventaire détaillé

| Fichier | Ligne | Objet | Opération |
|---|---:|---|---|
| `frontend/src/auth/AuthProvider.tsx` | 105 | `mon_contexte` | rpc |
| `frontend/src/auth/droits.ts` | 88 | `mes_droits_projets` | rpc |
| `frontend/src/auth/droits.ts` | 106 | `mes_droits_projets` | rpc |
| `frontend/src/hooks/useFoncier.ts` | 117 | `parcelles` | select |
| `frontend/src/hooks/useFoncier.ts` | 129 | `parcelles` | select |
| `frontend/src/hooks/useFoncier.ts` | 137 | `v_ug_parcelles` | select |
| `frontend/src/hooks/useFoncier.ts` | 242 | `personnes` | select |
| `frontend/src/hooks/useFoncier.ts` | 279 | `personnes` | insert |
| `frontend/src/hooks/useFoncier.ts` | 310 | `parcelles` | insert |
| `frontend/src/hooks/useFoncier.ts` | 338 | `parcelles` | update |
| `frontend/src/hooks/useFoncier.ts` | 362 | `parcelles` | delete |
| `frontend/src/hooks/useFoncier.ts` | 376 | `droits_fonciers` | insert |
| `frontend/src/hooks/useFoncier.ts` | 411 | `droits_fonciers` | update |
| `frontend/src/hooks/useFoncier.ts` | 447 | `droits_fonciers` | delete |
| `frontend/src/hooks/useFoncier.ts` | 463 | `droit_documents` | insert |
| `frontend/src/hooks/useSupabase.ts` | 272 | `v_projets_enrichis` | select |
| `frontend/src/hooks/useSupabase.ts` | 303 | `v_frontend_mesures_projets` | select |
| `frontend/src/hooks/useSupabase.ts` | 349 | `v_frontend_mesures_projets` | select |
| `frontend/src/hooks/useSupabase.ts` | 387 | `v_action_portefeuille` | select |
| `frontend/src/hooks/useSupabase.ts` | 410 | `v_portefeuille_organisation` | select |
| `frontend/src/hooks/useSupabase.ts` | 474 | `v_frontend_mesures_projets` | select |
| `frontend/src/hooks/useSupabase.ts` | 489 | `v_jalons_a_venir` | select |
| `frontend/src/hooks/useSupabase.ts` | 505 | `mv_stats_geomce` | select |
| `frontend/src/hooks/useSupabase.ts` | 518 | `v_frontend_mesures_par_departement` | select |
| `frontend/src/pages/contacts/api.ts` | 50 | `personnes` | select |
| `frontend/src/pages/contacts/api.ts` | 74 | `role_contact_ref` | select |
| `frontend/src/pages/contacts/api.ts` | 88 | `personnes` | select |
| `frontend/src/pages/contacts/api.ts` | 112 | `personnes` | select |
| `frontend/src/pages/contacts/api.ts` | 121 | `personnes` | select |
| `frontend/src/pages/contacts/api.ts` | 133 | `personnes` | select |
| `frontend/src/pages/contacts/api.ts` | 155 | `personnes` | select |
| `frontend/src/pages/contacts/api.ts` | 184 | `personnes` | insert |
| `frontend/src/pages/contacts/api.ts` | 208 | `personnes` | update |
| `frontend/src/pages/contacts/api.ts` | 228 | `projet_contact` | select |
| `frontend/src/pages/contacts/api.ts` | 288 | `v_contact_projet` | select |
| `frontend/src/pages/contacts/api.ts` | 305 | `projet_contact` | insert |
| `frontend/src/pages/contacts/api.ts` | 341 | `personnes` | select |
| `frontend/src/pages/contacts/api.ts` | 362 | `personnes` | update |
| `frontend/src/pages/contacts/api.ts` | 370 | `personnes` | insert |
| `frontend/src/pages/contacts/api.ts` | 401 | `personnes` | select |
| `frontend/src/pages/contacts/api.ts` | 418 | `personnes` | insert |
| `frontend/src/pages/contacts/api.ts` | 441 | `personnes` | update |
| `frontend/src/pages/contacts/api.ts` | 453 | `projet_contact` | delete |
| `frontend/src/pages/contacts/api.ts` | 491 | `personnes` | update |
| `frontend/src/pages/contacts/api.ts` | 503 | `personnes` | insert |
| `frontend/src/pages/contacts/api.ts` | 553 | `projet_contact` | select |
| `frontend/src/pages/contacts/api.ts` | 576 | `projet_contact` | delete |
| `frontend/src/pages/contacts/api.ts` | 589 | `personnes` | select |
| `frontend/src/pages/contacts/api.ts` | 596 | `projet_contact` | delete |
| `frontend/src/pages/contacts/api.ts` | 610 | `projet_contact` | select |
| `frontend/src/pages/contacts/api.ts` | 626 | `projet_contact` | delete |
| `frontend/src/pages/contacts/api.ts` | 633 | `projet_contact` | delete |
| `frontend/src/pages/contacts/api.ts` | 646 | `projet_contact` | update |
| `frontend/src/pages/contacts/api.ts` | 662 | `v_action_portefeuille` | select |
| `frontend/src/pages/contacts/api.ts` | 688 | `projets` | select |
| `frontend/src/pages/contacts/api.ts` | 699 | `projets` | select |
| `frontend/src/pages/contacts/useCurrentPersonne.ts` | 39 | `personnes` | select |
| `frontend/src/pages/fil/api.ts` | 84 | `personnes` | select |
| `frontend/src/pages/fil/api.ts` | 136 | `note_interne` | select |
| `frontend/src/pages/fil/api.ts` | 167 | `note_interne` | insert |
| `frontend/src/pages/fil/api.ts` | 197 | `note_interne` | update |
| `frontend/src/pages/fil/api.ts` | 231 | `v_fil_interne` | select |
| `frontend/src/pages/fil/api.ts` | 256 | `documents` | select |
| `frontend/src/pages/fil/api.ts` | 267 | `projets` | select |
| `frontend/src/pages/projet/page_projet/api/credits.ts` | 101 | `site_agrement` | select |
| `frontend/src/pages/projet/page_projet/api/credits.ts` | 122 | `v_credit_stock` | select |
| `frontend/src/pages/projet/page_projet/api/credits.ts` | 148 | `v_registre_ventes` | select |
| `frontend/src/pages/projet/page_projet/api/credits.ts` | 176 | `v_credit_suivi` | select |
| `frontend/src/pages/projet/page_projet/api/credits.ts` | 199 | `v_registre_ventes` | select |
| `frontend/src/pages/projet/page_projet/api/credits.ts` | 231 | `v_tresorerie_site` | select |
| `frontend/src/pages/projet/page_projet/api/supabaseSources.ts` | 61 | `v_projets_enrichis` | select |
| `frontend/src/pages/projet/page_projet/api/supabaseSources.ts` | 72 | `v_frontend_mesures_projets` | select |
| `frontend/src/pages/projet/page_projet/api/supabaseSources.ts` | 87 | `v_frontend_mesures_projets` | select |
| `frontend/src/pages/projet/page_projet/components/calendar/etapes/api.ts` | 68 | `action_etape` | select |
| `frontend/src/pages/projet/page_projet/components/calendar/etapes/api.ts` | 83 | `action_etape` | insert |
| `frontend/src/pages/projet/page_projet/components/calendar/etapes/api.ts` | 101 | `action_etape` | update |
| `frontend/src/pages/projet/page_projet/components/calendar/etapes/api.ts` | 111 | `action_etape` | delete |
| `frontend/src/pages/projet/page_projet/components/calendar/etapes/api.ts` | 118 | `action_etape` | update |
| `frontend/src/pages/projet/page_projet/components/calendar/etapes/api.ts` | 126 | `checklist_modele` | select |
| `frontend/src/pages/projet/page_projet/components/calendar/etapes/api.ts` | 146 | `appliquer_checklist_modele` | rpc |
| `frontend/src/pages/projet/page_projet/components/calendar/etapes/api.ts` | 159 | `creer_modele_depuis_action` | rpc |

`v_frontend_mesures_projets` et `v_frontend_mesures_par_departement` sont passés par constante (`MESURES_PROJETS_VIEW`, `MESURES_PAR_DEPT_VIEW`), d'où l'absence de guillemets dans le source.

### 2.2 Tables et vues touchées — statut attendu

Le statut attendu est celui du modèle `backend/db/028_rls.sql` et des migrations suivantes de `backend/db/`, **si** les scripts V0 plus anciens n'ont pas laissé leurs policies en place (voir § 4). La colonne « Constat » est à remplir après les requêtes du § 5.

| Objet | Opérations front | Statut attendu si `028` a le dernier mot | Pré-classement | Constat live |
|---|---|---|---|---|
| `personnes` | select, insert, update | RLS forcée. Lecture et écriture limitées à `prive.mes_appartenances()`, rôle `authenticated` | critique | |
| `parcelles` | select, insert, update, delete | RLS forcée. Projet lisible, ou portefeuille d'organisation si `projet_id` est nul. Écriture niveau ≥ 3 | critique | |
| `droits_fonciers` | insert, update, delete | RLS forcée, périmètre organisation, `authenticated` | critique | |
| `droit_documents` | insert | idem | critique | |
| `projet_contact` | select, insert, update, delete | RLS forcée, table « projet » standard (`projets_lisibles` / niveau ≥ 3) | critique | |
| `action_etape` | select, insert, update, delete | idem | critique | |
| `checklist_modele` | select (+ insert via rpc) | RLS forcée. Lecture membres de l'organisation, écriture `admin` | critique | |
| `note_interne` | select, insert, update | RLS forcée, interne à l'organisation autrice, `authenticated` | critique | |
| `projets` | select | RLS forcée. Lecture `prive.projets_lisibles()`. Insert / update / delete réservés aux membres selon le niveau | critique | |
| `documents` | select | RLS forcée, lecture du projet. Suppression réservée au niveau ≥ 4 | à vérifier | |
| `site_agrement` | select | RLS forcée, table projet standard | à vérifier | |
| `role_contact_ref` | select | Référentiel : `SELECT` pour `authenticated` avec `USING (true)`, pas d'écriture | à vérifier | |
| `v_ug_parcelles` | select | Vue `security_invoker`. Les `CREATE OR REPLACE` de `041`/`050`/`052` ne portent pas l'option | critique | |
| `v_contact_projet` | select | `security_invoker` dans `046` / `049`. Hérite des policies des tables sous-jacentes | critique | |
| `v_fil_interne` | select | `security_invoker` dans `045`. Hérite de `note_interne` | critique | |
| `v_action_portefeuille` | select | `security_invoker` (`030z`, `047`, `055`). `REVOKE` à `anon` | à vérifier | |
| `v_credit_stock` | select | Vue financière. `security_invoker` visé par `055` / `063` | critique | |
| `v_credit_suivi` | select | idem | critique | |
| `v_registre_ventes` | select | idem | critique | |
| `v_tresorerie_site` | select | idem, `security_invoker` + `REVOKE` anon dans `063` | critique | |
| `v_frontend_mesures_projets` | select | Catalogue GEOMCE (`geomce_surf/lin/pct`). Le SQL du dépôt (`frontend/v_frontend_mesures_projets.sql`) ne pose pas `security_invoker`. Les tables sources sont des référentiels en lecture libre pour `authenticated` | à vérifier | |
| `v_frontend_mesures_par_departement` | select | Agrégat du catalogue. Même réserve | à vérifier | |
| `mv_stats_geomce` | select | Vue matérialisée : pas de RLS possible. Absente du SQL du dépôt. Le type front ne contient que des compteurs | à vérifier | |
| `v_projets_enrichis` | select | Absente du SQL du dépôt. Porte des champs projet (documents, jalons, surfaces) | critique | |
| `v_jalons_a_venir` | select | Absente du SQL du dépôt. Porte `projet_id`, `organisation_id`, titres | critique | |
| `v_portefeuille_organisation` | select | Absente du SQL du dépôt | critique | |
| `mon_contexte` | rpc | `SECURITY INVOKER`. `EXECUTE` retiré à `anon`, accordé à `authenticated` (`054`) | à vérifier | |
| `mes_droits_projets` | rpc | `SECURITY INVOKER`. Même grants. Appelle des fonctions `SECURITY DEFINER` du schéma `prive` | à vérifier | |
| `appliquer_checklist_modele` | rpc | `SECURITY INVOKER` (défaut, non déclaré). `044` fait `GRANT EXECUTE` à `anon` | critique | |
| `creer_modele_depuis_action` | rpc | idem, et insère dans `checklist_modele` | critique | |

`checklist_modele_etape` n'est pas lue directement par le front. Les deux RPC ci-dessus y écrivent. Elle a, dans `044`, une policy `v0_open` `FOR ALL USING (true)`.

## 3. Accès base du backend

Deux chemins, distincts du front.

### 3.1 Postgres direct (psycopg)

| Chemin | Rôle | RLS |
|---|---|---|
| `connect_utilisateur` (`backend/api/db/utilisateur.py`) | Connexion `kererc_backend` (`DATABASE_URL_BACKEND`, `LOGIN NOINHERIT`, `backend/db/000_roles.sql`), puis `SET LOCAL ROLE authenticated` et `request.jwt.claims` | La RLS s'applique comme pour un JWT utilisateur |
| `get_database_url()` | Rôle `postgres` (`SUPABASE_USER` dans `backend/.env`) | Superuser : la RLS ne s'applique pas, même forcée |

Le CRUD métier (projets, documents, budget, foncier, cadastre, géométries, OCR, parc, contrôle, dialogue, carto, satellite, planning, journal, transfert, bilan) passe par `connect_utilisateur`.

Contournements volontaires, côté serveur, après contrôle applicatif. Ils ne sont pas appelables avec la clé anon :

- `backend/api/admin/service.py` via `_connect()` → `postgres` : invitations, création d'organisation, listes membres / organisations / invitations, révocation, changement de rôle. Auth Admin (`invite_user_by_email`, `delete_user`) passe par `get_supabase_admin()`.
- `backend/api/acces/router.py`, `_completer_identites` : lecture `auth.users` et `bancarisation.organisations` en `postgres`, pour compléter des e-mails déjà filtrés par `connect_utilisateur`.
- Scripts hors API : `backend/scripts/generer_projet_mock.py`, `backend/scripts/matching_geometries_et_ug.py`.

`bancarisation.invitation` (`057`) active la RLS sans la forcer et sans policy : le module admin en `postgres` est le seul chemin d'écriture prévu. La requête 5b doit confirmer que `anon` n'y a aucun droit.

### 3.2 supabase-py (`service_role`)

`get_supabase()` est un alias de `get_supabase_admin()`. Aucun `.from()` / `.schema()` métier n'appelle ce client. Usages trouvés :

- Storage, bucket `documents-projet` : `backend/api/documents/crud_document.py`, `backend/api/satellite/crud.py` (upload, download, remove, URL signée).
- Auth Admin : invitations et suppression de compte.

La table `bancarisation.documents` est lue par le front en `select`. Les fichiers du bucket passent par le backend. Le bucket reste à contrôler (requête 5g) : le schéma `storage` est en général dans `PGRST_DB_SCHEMAS`, donc joignable avec la clé anon si la RLS Storage est ouverte.

## 4. Migrations du dépôt

Deux séries coexistent.

**Série cible** — `backend/db/`, notamment `028_rls.sql` :

- `REVOKE ALL` sur toutes les tables, séquences et quelques fonctions de `bancarisation` pour `anon`, et privilèges par défaut futurs retirés à `anon` **pour le rôle qui exécute le script**.
- `GRANT` large à `authenticated`, puis retraits ciblés (`journal_audit`, `profils`).
- RLS activée et forcée, policies `TO authenticated`.
- Toutes les vues existantes à ce moment passées en `security_invoker`.
- Le script échoue si une table de `bancarisation` reste sans RLS forcée.
- `backend/db/tests/isolation.sql` encode l'état sain : RLS forcée partout, vues `security_invoker`, aucune policy `auth.uid() IS NULL`, aucune policy `FOR ALL`, policies réservées à `authenticated`, `USING (true)` limité aux référentiels, aucun `GRANT` table pour `anon`.

Tables créées après `028` dans cette série : `occurrence_finance` (RLS forcée, `voit_finances`), `occurrence_responsable` (RLS forcée), `transfert_projet` (RLS forcée, `REVOKE` à `anon` et `authenticated` puis `SELECT` seul à `authenticated`), `invitation` (RLS activée, **non forcée**, aucune policy).

**Série antérieure, encore dans le dépôt** — `backend/api/ocr/db/sql/` et `frontend/src/pages/projet/page_projet/components/foncier/sql/foncier_init.sql`. Elle contient des policies que `028_rls.sql` ne supprime pas, parce qu'il ne retire que les noms `{table}_select|insert|update|delete`. Postgres **additionne** les policies (OU). Une ancienne policy ouverte reste efficace à côté des policies `028`.

Policies ouvertes, sans clause `TO` (donc valables pour `PUBLIC`, y compris `anon`) :

| Source | Policy | Effet |
|---|---|---|
| `foncier_init.sql` | `v0_open` sur `personnes`, `parcelles`, `ug_surf_parcelles`, `ug_lin_parcelles`, `ug_pct_parcelles`, `droits_fonciers`, `droit_documents` | `FOR ALL USING (true) WITH CHECK (true)` |
| `044_action_etapes.sql` | `v0_open` sur `checklist_modele`, `checklist_modele_etape` | idem |
| `044_action_etapes.sql` | `action_etape_lecture`, `action_etape_ecriture` | `auth.uid() IS NULL` autorise l'anonyme |
| `043_contacts.sql` | `projet_contact_lecture`, `projet_contact_ecriture` | idem |
| `045_notes_internes.sql` | `note_interne_lecture`, `note_interne_ecriture` | idem |
| `018_roles_droits.sql` | `projets_lecture`, `projets_ecriture`, et `{table}_lecture` / `{table}_ecriture` sur `occurrence`, `rapport_bilan`, `budget_mouvement`, `budget_baseline`, `ligne_budget` | `FOR ALL` ou `SELECT` sans `TO authenticated`, via `peut_lire_projet` / `peut_ecrire_projet` |

Les mêmes fichiers font `GRANT` à `anon` (tables, vues, `EXECUTE`). `028` révoque les grants présents **au moment où il tourne**. Un script V0 rejoué ensuite, ou des privilèges par défaut d'un autre rôle (`supabase_admin` / `postgres`), les rétablit. `GRANT ... TO anon` explicites repérés : `043`, `044`, `045`, `046`, `048`, `049`, `050`, `052`, `foncier_init.sql`, `foncier_v_ug_parcelles.sql`.

`045` crée aussi `bancarisation.peut_lire_interne(uuid)` en `SECURITY DEFINER` et `GRANT EXECUTE` à `anon`. Une fonction `SECURITY DEFINER` dans un schéma exposé s'exécute avec les droits du propriétaire et traverse la RLS.

Le dépôt ne permet pas de savoir lequel des deux états est en base. C'est l'objet des requêtes suivantes.

## 5. Requêtes de diagnostic

Lecture seule. À lancer dans l'éditeur SQL de Studio, connecté en rôle capable de lire les catalogues (`postgres`).

`PGRST_DB_SCHEMAS` n'est pas dans le dépôt. Dans chaque requête qui contient la liste, la remplacer par la valeur réelle (variable d'environnement du conteneur PostgREST / Kong). Le front n'utilise que `bancarisation`. Les défauts Supabase ajoutent souvent `public`, `storage`, `graphql_public`. La liste écrite ci-dessous est un point de départ :

```text
bancarisation, public, storage, graphql_public
```

Le schéma `prive` porte les fonctions de droits (`SECURITY DEFINER`). S'il apparaît dans `PGRST_DB_SCHEMAS`, le classer critique avant même de lire les policies.

### 5a. Schémas candidats et liste à figer

```sql
-- Schémas sur lesquels anon, authenticated ou authenticator ont un droit.
-- Ce n'est pas PGRST_DB_SCHEMAS : c'est un indice. La liste réelle est celle du conteneur rest.
SELECT n.nspname AS schema,
       r.rolname AS role,
       string_agg(DISTINCT a.privilege_type, ', ' ORDER BY a.privilege_type) AS privileges
FROM pg_namespace n
JOIN LATERAL aclexplode(n.nspacl) a ON true
JOIN pg_roles r ON r.oid = a.grantee
WHERE r.rolname IN ('anon', 'authenticated', 'authenticator', 'PUBLIC')
GROUP BY n.nspname, r.rolname
ORDER BY 1, 2;
```

### 5b. Tables : RLS, forçage, nombre de policies

```sql
SELECT n.nspname AS schema,
       c.relname AS table,
       c.relrowsecurity AS rls_active,
       c.relforcerowsecurity AS rls_force,
       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS nb_policies,
       pg_get_userbyid(c.relowner) AS proprietaire
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('bancarisation', 'public', 'storage', 'graphql_public')  -- PGRST_DB_SCHEMAS
  AND c.relkind IN ('r', 'p')
ORDER BY n.nspname, c.relname;
```

Lecture : une ligne avec `rls_active = false` sur un schéma exposé est ouverte dès qu'un `GRANT` existe pour `anon`, `authenticated` ou `PUBLIC`. `rls_force = false` laisse le propriétaire de la table passer au travers. PostgREST utilise `anon` / `authenticated`, pas le propriétaire : le trou immédiat est `rls_active = false`. `invitation` est le cas connu du dépôt (RLS activée, non forcée, zéro policy).

### 5c. Détail des policies

```sql
SELECT schemaname,
       tablename,
       policyname,
       roles,
       cmd,
       qual AS using_expr,
       with_check
FROM pg_policies
WHERE schemaname IN ('bancarisation', 'public', 'storage', 'graphql_public')  -- PGRST_DB_SCHEMAS
ORDER BY schemaname, tablename, policyname;
```

Anomalies à isoler (le résultat doit être vide si `isolation.sql` passerait) :

```sql
SELECT schemaname, tablename, policyname, roles, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'bancarisation'
  AND (
    coalesce(qual, '') || coalesce(with_check, '') ILIKE '%auth.uid() IS NULL%'
    OR cmd = 'ALL'
    OR roles <> ARRAY['authenticated']::name[]
    OR (
      (qual = 'true' OR with_check = 'true')
      AND tablename NOT IN (
        'ref_geomce_categorie', 'role_contact_ref', 'parametre_signal', 'prestataires',
        'geomce_communes', 'geomce_lin', 'geomce_pct', 'geomce_surf'
      )
    )
    OR coalesce(qual, '') || coalesce(with_check, '')
         ~ '(peut_lire_projet|peut_ecrire_projet|peut_lire_interne|role_courant|organisation_courante)'
    OR policyname IN ('v0_open')
    OR policyname LIKE '%\_lecture' ESCAPE '\'
    OR policyname LIKE '%\_ecriture' ESCAPE '\'
  )
ORDER BY tablename, policyname;
```

Une ligne ici signifie que l'accès anonyme ou public est encore autorisé sur cette table, y compris si une policy `028` plus stricte existe à côté.

### 5d. GRANT à `anon` et `authenticated`

Tables et vues :

```sql
SELECT grantee, table_schema, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee IN ('anon', 'authenticated', 'PUBLIC')
  AND table_schema IN ('bancarisation', 'public', 'storage', 'graphql_public')  -- PGRST_DB_SCHEMAS
ORDER BY table_schema, table_name, grantee, privilege_type;
```

Séquences :

```sql
SELECT grantee, object_schema, object_name, object_type, privilege_type
FROM information_schema.usage_privileges
WHERE grantee IN ('anon', 'authenticated', 'PUBLIC')
  AND object_type = 'SEQUENCE'
  AND object_schema IN ('bancarisation', 'public', 'storage', 'graphql_public')  -- PGRST_DB_SCHEMAS
ORDER BY 2, 3, 1;
```

Fonctions :

```sql
SELECT grantee, specific_schema, routine_name, privilege_type
FROM information_schema.routine_privileges
WHERE grantee IN ('anon', 'authenticated', 'PUBLIC')
  AND specific_schema IN ('bancarisation', 'public', 'storage', 'graphql_public')  -- PGRST_DB_SCHEMAS
ORDER BY specific_schema, routine_name, grantee;
```

Privilèges par défaut (ce que recevra la prochaine table créée dans le schéma) :

```sql
SELECT n.nspname AS schema,
       r.rolname AS role_createur,
       d.defaclobjtype AS type_objet,
       d.defaclacl AS acl
FROM pg_default_acl d
JOIN pg_namespace n ON n.oid = d.defaclnamespace
JOIN pg_roles r ON r.oid = d.defaclrole
WHERE n.nspname IN ('bancarisation', 'public', 'storage', 'graphql_public')  -- PGRST_DB_SCHEMAS
ORDER BY 1, 2, 3;
```

`defaclobjtype` : `r` table, `S` séquence, `f` fonction. Un ACL contenant `anon=` est un futur trou.

Contrôle rapide aligné sur le test d'isolation :

```sql
SELECT table_schema, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'anon'
  AND table_schema = 'bancarisation'
ORDER BY 1, 2, 3;
```

Résultat attendu : zéro ligne.

### 5e. Fonctions `SECURITY DEFINER` dans les schémas exposés

```sql
SELECT n.nspname AS schema,
       p.proname AS fonction,
       pg_get_function_identity_arguments(p.oid) AS arguments,
       pg_get_userbyid(p.proowner) AS proprietaire,
       p.proconfig AS config,
       has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_execute,
       has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authenticated_execute,
       has_function_privilege('public', p.oid, 'EXECUTE') AS public_execute
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname IN ('bancarisation', 'public', 'storage', 'graphql_public')  -- PGRST_DB_SCHEMAS
  AND p.prosecdef
ORDER BY 1, 2;
```

Toute ligne avec `anon_execute` ou `public_execute` est à traiter comme critique : l'appel `/rest/v1/rpc/<fonction>` tourne avec les droits du propriétaire. Les fonctions de `prive` n'apparaissent ici que si `prive` a été ajouté à la liste exposée — ce qu'il ne faut pas faire.

### 5f. Vues et vues matérialisées

```sql
SELECT n.nspname AS schema,
       c.relname AS nom,
       CASE c.relkind WHEN 'v' THEN 'vue' WHEN 'm' THEN 'materialisee' END AS genre,
       c.reloptions,
       COALESCE(c.reloptions @> ARRAY['security_invoker=true'], false) AS security_invoker,
       pg_get_userbyid(c.relowner) AS proprietaire
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('bancarisation', 'public', 'storage', 'graphql_public')  -- PGRST_DB_SCHEMAS
  AND c.relkind IN ('v', 'm')
ORDER BY 1, 3, 2;
```

Une vue avec `security_invoker = false` s'exécute en tant que propriétaire (souvent `postgres`) et ignore la RLS des tables qu'elle lit. Une vue matérialisée n'a pas de RLS : la protéger se fait par `REVOKE` à `anon` / `authenticated`, ou en la sortant du schéma exposé.

Focus sur ce que le front sélectionne :

```sql
SELECT c.relname,
       c.relkind,
       COALESCE(c.reloptions @> ARRAY['security_invoker=true'], false) AS security_invoker,
       has_table_privilege('anon', c.oid, 'SELECT') AS anon_select,
       has_table_privilege('authenticated', c.oid, 'SELECT') AS authenticated_select
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'bancarisation'
  AND c.relname IN (
    'v_ug_parcelles', 'v_contact_projet', 'v_fil_interne', 'v_action_portefeuille',
    'v_credit_stock', 'v_credit_suivi', 'v_registre_ventes', 'v_tresorerie_site',
    'v_frontend_mesures_projets', 'v_frontend_mesures_par_departement',
    'mv_stats_geomce', 'v_projets_enrichis', 'v_jalons_a_venir',
    'v_portefeuille_organisation'
  )
ORDER BY c.relname;
```

Une vue absente de ce résultat n'existe pas en base (le front recevra une erreur PostgREST, pas une fuite).

### 5g. Storage (schéma exposé habituel, aucun appel front)

```sql
SELECT id, name, public
FROM storage.buckets
ORDER BY name;

SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS nb_policies
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'storage'
  AND c.relname IN ('objects', 'buckets')
  AND c.relkind = 'r';

SELECT tablename, policyname, roles, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'storage'
  AND tablename IN ('objects', 'buckets')
ORDER BY tablename, policyname;
```

Le bucket applicatif est `documents-projet`. `public = true` rend les fichiers lisibles sans policy. Le backend y accède avec la `service_role`, qui ne tient pas compte de ces policies.

## 6. Classification

À recopier une fois les résultats collés. Règle de lecture, dans cet ordre :

1. **Critique** — sur un schéma exposé : RLS inactive et `GRANT` à `anon` ou `PUBLIC` ; ou policy `v0_open` / `auth.uid() IS NULL` / `FOR ALL` sans `TO authenticated` encore présente ; ou vue lue par le front avec `security_invoker = false` et `anon_select = true` ; ou fonction `SECURITY DEFINER` exécutable par `anon` ou `PUBLIC`.
2. **À vérifier** — RLS active mais non forcée ; `GRANT` à `anon` alors que les policies semblent saines (le grant est inutile si aucune policy ne vise `anon`, il reste un filet manquant) ; vue matérialisée ; référentiel `USING (true)` en `SELECT` seul ; objet attendu absent du résultat.
3. **OK** — RLS active et forcée, policies uniquement `TO authenticated`, aucun `GRANT` table/vue/fonction pour `anon` sur `bancarisation`, vues du front en `security_invoker`.

| Contrôle | Pré-classement (code) | Constat live | Classe finale |
|---|---|---|---|
| Clé `service_role` dans le front ou une variable `VITE_*` | OK | aucune occurrence dans le dépôt | OK |
| Policies V0 (`v0_open`, `*_lecture`, `*_ecriture`, `auth.uid() IS NULL`) encore en base | critique | requête 5c, second SELECT | |
| `GRANT` de `anon` sur `bancarisation` | critique | requête 5d, dernier SELECT | |
| Tables de `bancarisation` sans RLS | critique | requête 5b, `rls_active = false` | |
| `invitation` : RLS non forcée, zéro policy | à vérifier | requête 5b | |
| Vues front `security_invoker = false` | critique | requête 5f, second SELECT | |
| `v_projets_enrichis`, `v_jalons_a_venir`, `v_portefeuille_organisation` (absentes du SQL) | critique | requête 5f : existent-elles, et avec quelle option | |
| Catalogue GEOMCE (`v_frontend_mesures_*`, `mv_stats_geomce`) | à vérifier | requête 5f. Données de référentiel, fuite moindre si les tables `geomce_*` sont bien en lecture seule | |
| RPC `mon_contexte`, `mes_droits_projets` exécutables par `anon` | à vérifier | requête 5d fonctions / 5e | |
| RPC checklist exécutables par `anon` | critique | requête 5d fonctions | |
| `SECURITY DEFINER` dans un schéma exposé, `EXECUTE` pour `anon` | critique | requête 5e. `peut_lire_interne` est le cas écrit dans `045` | |
| Schéma `prive` présent dans `PGRST_DB_SCHEMAS` | critique | valeur réelle de la variable | |
| Bucket `documents-projet` public ou `storage.objects` sans RLS | à vérifier | requête 5g. Pas d'appel Storage côté front | |
| Backend `postgres` / `service_role` | hors clé anon | contournement serveur, déjà identifié au § 3 | hors périmètre anon |

Le pré-classement « critique » du tableau ne signifie pas que la base de dev est ouverte aujourd'hui. Il signifie que le dépôt contient encore le script qui ouvre l'accès, et que `028_rls.sql` ne retire pas ces policies par leur nom. Seules les requêtes du § 5 tranchent.
