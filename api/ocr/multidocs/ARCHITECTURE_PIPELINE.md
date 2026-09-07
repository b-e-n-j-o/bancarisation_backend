# Pipeline multidocs — système obtenu

Ce fichier décrit **ce qui tourne aujourd’hui** dans `api/ocr/multidocs`,
après le chantier « DAG + semoir, doc-par-doc, superviseur borné ».

Ce n’est pas un orchestrateur général (ReAct, outils libres, boucle jusqu’à
satisfaction). C’est un **DAG fixe** : chaque étage a un contrat, un budget
d’appels, et un mode d’échec opposable métier.

---

## En une phrase

Le LLM **écrit des règles** (fiches, récurrences, T0, rattachement UG) ;
Python **découpe, vérifie, sème les dates** et **refuse ce qui n’est pas dans
le référentiel SIG**. Un superviseur relit **seulement les trous**, borné.

---

## Pourquoi ce dessin (et pas un agent libre)

Les systèmes d’extraction documentaire qui tiennent en prod séparent :

| Couche | Qui | Quoi |
|---|---|---|
| Cartographie | LLM small, **un fichier à la fois** | rôles, T0, signaux |
| Extraction | LLM lourd **dans** un extracteur versionné | claims typés, ancrés |
| Contrôle | Python | ancres, UG ∈ géométries, JSONL |
| Rattrapage | LLM small **ciblé**, max 5 appels | trous listés, pas d’exploration |
| Calendrier | Python (semoir) | occurrences datées — **0 LLM** |

Un orchestrateur qui « décide de la suite » sur 70 pages d’arrêté + plan de
gestion produit de la **couverture partielle silencieuse** : il saute une
fiche, invente une UG, ou pire **écrit des dates** que le chargé de projet
ne peut plus opposer au plan.

Ici, si ça manque, c’est dans `superviseur.json` (`trous_restants`). Si une
date est fausse, c’est le semoir (rejouable). Si une UG est inventée, elle
est **rejetée** avant ingestion.

---

## Chaîne réelle (API `POST …/analyse-multidocs`)

```
documents déposés + géométries UG (étape 1 du wizard)
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ 1 · Normalisation          etape1_normalisation              │
│     PDF/XLSX/DOCX/ZIP… → blocs markdown + ancres (⟦p12⟧)     │
│     artefact : corpus.json                                   │
├──────────────────────────────────────────────────────────────┤
│ 2 · Triage doc-par-doc     etape2_triage.cartographier       │
│     1 appel small / fichier si N>1, puis fusion              │
│     rôles × plages, paramètres (T0, durée), signaux          │
│     artefact : carte.json                                    │
├──────────────────────────────────────────────────────────────┤
│ 3 · Plan                   etape3_planification (0 LLM)      │
│     carte + registre familles.py → jobs ordonnés             │
│     l’arrêté n’a PAS le droit d’écrire le calendrier         │
│     artefact : plan.json                                     │
├──────────────────────────────────────────────────────────────┤
│ 4 · Extraction             etape4 → extracteurs              │
│     plan_gestion v2 : distributeur (bornes + échéances)      │
│                     + slice Python + scribes                 │
│     artefact : claims.jsonl  (action, echeance_regle, …)     │
├──────────────────────────────────────────────────────────────┤
│ 5 · Superviseur borné      superviseur.rattraper             │
│     audit des trous → outils ciblés (≤ 3 tours, ≤ 5 LLM)     │
│     artefacts : superviseur.json, dossier.json (horizon)     │
├──────────────────────────────────────────────────────────────┤
│ 6 · Semoir + ingestion     claims_vers_calendrier (0 LLM)    │
│     échéances → occurrences datées → Postgres                │
│     artefacts : actions.json, occurrences.json               │
└──────────────────────────────────────────────────────────────┘
```

CLI : `python3 -m api.ocr.multidocs.run dossier/ --executer`
(s’arrête après le rattrapage ; l’ingestion API n’est pas dans le CLI).

---

## 1. Triage doc-par-doc

**Fichier :** `etape2_triage.py`

Un seul PDF historique : un appel, comme avant.

Plusieurs fichiers : **chaque document est classé isolément**. Un devis mal
lu ne peut plus étiqueter le plan de gestion en `estimation_budget`. Les
cartes sont fusionnées :

- segments concaténés ;
- paramètres : on garde la valeur à **plus haute confiance** par clé
  (`annee_t0`, `duree_ans`, …) ;
- documents non classés = union.

Garde-fou inchangé : locator halluciné → segment écarté. Pour un long plan
de gestion, 1–3 pages d’aperçu ne doivent pas tronquer l’extraction : on
bascule sur **document entier**.

---

## 2. Calendrier indépendant des ancres OCR

**Fichier :** `extractions/extract_plan_gestion_v2.py` (extracteur
`plan_gestion` v0.3.0)

Contrat métier :

> Le LLM produit des **règles de récurrence** (`echeance`).
> Python (semoir) produit les **occurrences datées**.
> Les citations OCR (`debut` / `fin_exclusive`) servent à **couper le texte
> de la fiche**, pas à autoriser le calendrier.

Comportement :

1. Le distributeur (LLM lourd) émet fiches + échéances.
2. Python coupe : d’abord les bornes LLM, **sinon le titre** `## TU2`
   (hashes markdown ignorés, apostrophes normalisées).
3. Scribe (LLM small) seulement si le chunk ≥ 80 caractères.
4. **Les échéances sont toujours émises**, même si la fiche n’a pas de
   texte localisé.
5. Si des échéances pointent un code sans action → **stub** d’action
   (calendrier ingérable, fiche à confirmer).

Échec typique d’avant : ancres `#### TU1` vs OCR `## TU1` → 0 action, 20
échéances orphelines, semoir à vide. Ce n’est plus bloquant.

---

## 3. Superviseur borné

**Fichier :** `superviseur.py`

Ce n’est **pas** un agent. C’est un audit Python + une liste fermée d’outils.

### Trous détectés

| Type | Condition | Outil |
|---|---|---|
| `fiche_sans_texte` | contenu < 80 car. ou « non localisé » | recoupe par code + scribe |
| `echeance_orpheline` | `code_operation` sans action parente | stub `ActionFiche` (0 LLM) |
| `ug_indefini` | `ug_ids = a_definir` **et** référentiel SIG non vide | `reconcilier_ug_ids` puis petit LLM **contraint aux codes SIG** |
| `t0_manquant` | pas d’année 1990–2100 dans carte/claims | LLM small sur les **6 premières pages** des plans |

### Bornes

- `MAX_TOURS = 3`
- `MAX_APPELS_LLM = 5`
- si le budget LLM est épuisé, le déterministe continue (stub, recoupe
  sans scribe, réconciliation UG par alias)

### Ce qu’il n’a pas le droit de faire

- générer des dates d’occurrence ;
- inventer une UG hors géométries du projet ;
- relire le dossier entier « pour voir » ;
- choisir un extracteur, relancer le DAG, appeler un outil hors liste.

Les UG proposées par le LLM passent **toujours** par
`domain/ug_ids.reconcilier_ug_ids`. Hors référentiel → `a_definir`, champ
`ug_ids` à confirmer.

Si une action est rattachée, les échéances orphelines **du même code**
héritent des `ug_ids` (le semoir exige `géométrie.ug_id ∈ ug_ids[]`).

### Horizon pour le semoir

Le triage et/ou le rattrapage T0 écrivent `multidocs/dossier.json`
(`horizon.annee_debut` / `duree_ans` / `annee_fin`). Le semoir lit ce
fichier **avant** de deviner l’horizon sur les échéances.

---

## 4. Contrat UG / géométries

Les unités de gestion **ne viennent pas du ZIP déposé avec les PDF**.

Elles viennent de l’étape **Géométries** du wizard (`charger_zones_sig` →
table `geometries`). Convention :

```
géométrie.ug_id  ∈  action.ug_ids[]  ∈  echeance.ug_ids[]  ∈  occurrence.ug_ids[]
```

Normalisation : `"UG 1"` / `"UG1"` → `ug1`. Sentinel d’incertitude :
`a_definir` (jamais une géométrie).

Un ZIP SHP dans le drop documents peut être **lu** (normalisation) mais
n’est **pas** injecté comme référentiel dans le même run. C’est voulu :
le référentiel opposable, c’est celui que l’utilisateur a confirmé sur la
carte.

---

## 5. Familles documentaires (inchangé, structurant)

`utils/familles.py` : ce qu’un rôle **a le droit d’écrire**.

| Famille | Exemple | Droit | Interdit |
|---|---|---|---|
| règles | plan de gestion, fiches | `action` + `echeance_regle` | — |
| obligations | arrêté, ORE | `prescription` | **jamais** d’occurrence |
| coût | estimation, devis | `ligne_budget` | pas d’échéance seul |
| réalisé | décompte, CR | statuts / montants | pas de règle |
| contexte | diagnostic, carto UG | paramètres, UG | pas de calendrier |

L’arrêté n’est **pas** une 2ᵉ source de calendrier. C’est une checklist
d’obligations, reliée ensuite en couverture n:n.

---

## Artefacts d’un run (`work/{projet_id}/multidocs/`)

| Fichier | Producteur | Usage |
|---|---|---|
| `corpus.json` | normalisation | rejeu / debug OCR |
| `carte.json` | triage (+ superviseur si T0) | plan, audit rôles |
| `plan.json` | planification | jobs lancés / non couverts |
| `claims.jsonl` | extraction puis superviseur | contrat unique vers le semoir |
| `superviseur.json` | rattrapage | trous initiaux / corrections / restants |
| `dossier.json` | superviseur (si T0 connu) | horizon du semoir |
| `actions.json` / `occurrences.json` | semoir | ingestion + UI |
| `debug/` | chaque appel LLM | prompts, réponses, erreurs |
| `journal.json` | `JournalPipeline` | durées, OCR, LLM par étape |

L’UI de chargement (`NouveauProjetPage`) affiche les étapes
`normalisation → triage → plan → extraction → rattrapage → occurrences`.

---

## Ce qui n’est toujours pas là

Opposable, pour ne pas faire croire que le DAG « fait tout le dossier BE » :

- **extracteur arrêté / budget / réalisé** : le registre existe, les jobs
  peuvent être planifiés, l’ingestion métier (prescriptions, lignes €,
  rejeu d’état) n’est pas branchée comme le calendrier ;
- **réconciliation inter-documents** : deux claims pour la même action
  restent deux claims (volontaire). Pas d’arbitre d’entité encore ;
- **ZIP SIG dans le même drop** que les PDF : pas fusionné au référentiel
  géométries du projet ;
- **patcheur / rejoueur** : une facture 2024 ne doit jamais décaler une
  récurrence — cet étage reste à construire, séparé du semoir ;
- **ingestion Postgres** : si la base est down, le run peut finir `done`
  côté claims + `superviseur.json`, avec un avertissement semoir. Relancer
  `claims_vers_calendrier` suffit, sans ré-OCR.

---

## Comment relire un échec

1. `journal.json` — quelle étape, combien d’appels.
2. `carte.json` — le plan a-t-il le rôle `plan_gestion` ?
3. `claims.jsonl` — y a-t-il des `echeance_regle` (calendrier) même sans
   belles fiches ?
4. `superviseur.json` — `trous_restants` : UG, T0, texte. C’est la liste
   à montrer au chargé de projet, pas un log modèle.
5. `debug/distributeur/` et `debug/scribe/` — citations vs OCR.

Si les échéances sont là et les occurrences absentes : semoir / Postgres,
pas le LLM.

---

## Fichiers clés

| Rôle | Chemin |
|---|---|
| Orchestration API | `multidocs/service.py` |
| Orchestration CLI | `multidocs/run.py` |
| Superviseur | `multidocs/superviseur.py` |
| Triage | `multidocs/etape2_triage.py` |
| Extracteur plan | `extractions/extract_plan_gestion_v2.py` |
| Semoir | `ocr/claims_vers_calendrier.py` |
| Contrat UG | `ocr/domain/ug_ids.py` |
| Droits d’écriture | `multidocs/utils/familles.py` |
