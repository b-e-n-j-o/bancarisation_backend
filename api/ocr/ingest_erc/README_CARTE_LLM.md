# La carte du document

> Le LLM décrit la structure du document. Le code fait l'extraction.

## Pourquoi

Les premières versions des extracteurs étaient écrites à partir d'un seul plan de gestion
(Le Barp). Elles cherchaient « UG 3 », « Unités de gestion : », « TU 1 », « Constat et
justification ». Sur un plan rédigé autrement — « Secteur S3 », « Localisation : »,
« Fiche GH 01 », « Modalités » — elles ne trouvaient plus rien.

Deux solutions étaient possibles :

1. tout confier au LLM, et perdre la traçabilité, la reproductibilité et le contrôle du coût ;
2. faire découvrir la structure par le LLM, une fois par document, puis appliquer cette
   structure par du code déterministe.

C'est la deuxième qui est retenue. Le LLM ne lit pas le contenu métier : il répond à la
question « comment ce document est-il fait ? ».

## Le principe en une phrase

**Un appel LLM produit une `CarteDocument`** (vocabulaire, fiches, tableaux, sections,
conventions). **Le code vérifie cette carte** contre le document, **en tire des règles regex**,
puis extrait comme avant — mais avec les mots du document, pas ceux de Le Barp.

## Les cinq étapes

### 1. Compaction — `carte.compacter(doc)`

Prépare le document entier pour le LLM :

- pages balisées `=== page N ===` ;
- en-têtes et pieds de page répétés supprimés (calculés à l'inventaire) ;
- images retirées ;
- tableaux de plus de 12 lignes, ou d'au moins 5 colonnes-années, réduits à leurs
  4 premières lignes suivies de `[… tableau tronqué : N lignes au total]`.

Les matrices tronquées sont précisément celles que l'OCR relit mal (planning 50 ans,
détail des coûts). Elles n'apportent rien à la compréhension de la structure.

Ordre de grandeur pour un plan de 102 pages : environ 250 000 caractères bruts,
50 à 70k tokens après compaction.

### 2. Appel LLM — `llm.cartographier(texte)`

Un seul appel, modèle réglable par `MISTRAL_MODEL_CARTE`. Au-delà de
`CARTE_BUDGET_TOKENS` (100k par défaut), le document est découpé en paquets de pages avec
recouvrement, et les cartes partielles sont fusionnées par `carte.fusionner`.

Le prompt impose trois choses :

- décrire la structure, **pas** extraire le contenu ;
- **copier** les libellés, titres, préfixes et citations, sans les reformuler ;
- ne rien inventer, laisser vide ce qui n'existe pas.

### 3. Vérification — `carte.verifier(carte, doc)`

Chaque élément annoncé est recherché dans le document, d'abord à la page indiquée, puis sur
les pages voisines. **Ce qui n'est pas retrouvé est écarté**, jamais utilisé, et listé dans
`carte.verification.ecartes`.

| Élément | Preuve exigée |
|---|---|
| Fiche | son titre, sur sa page ±2 |
| Tableau | tous les libellés de colonnes annoncés, sur sa page |
| Famille de codes | sa citation, ou à défaut le préfixe réellement écrit quelque part |
| Conventions (état zéro, N, fin) | au moins une citation retrouvée, sinon les années sont ignorées |

Le rapport donne, pour chaque catégorie, le nombre retenu sur le nombre annoncé. C'est
l'indicateur de confiance à regarder en premier après un run.

### 4. Règles dérivées — `carte.Regles.depuis(carte)`

La carte est convertie en motifs appliqués partout dans le pipeline :

| Règle | Construite à partir de | Exemple Le Barp | Exemple CEN |
|---|---|---|---|
| `re_unite` | préfixes d'unités | `UG 3` | `S3`, `secteur 3` |
| `canon_unite` | préfixe le plus court | `UG3` | `S3` |
| `re_code` | préfixes des familles | `TU 1` → `TU1` | `GH 01` → `GH1` |
| `champs` | libellés des champs de fiche | `Unités de gestion` | `Localisation` |
| `re_fin_entete` | fin de l'en-tête descriptif | `Constat et justification` | `Modalités` |

Deux commodités : `regles.unites(txt)` comprend les énumérations (« UG 2, UG 4 et 6 ») et
les plages (« S1 à S3 ») ; `regles.code(txt)` normalise les codes (zéros de tête retirés).

Sans clé Mistral, `Regles.defaut()` reproduit le comportement historique : le pipeline
fonctionne toujours, avec les règles calées sur Le Barp.

### 5. Extraction pilotée

Les règles et la carte sont passées aux extracteurs existants :

- **Fiches** : chaque fiche de la carte est localisée par son titre (on privilégie la page
  annoncée, une ligne de titre, une ligne portant le code), puis le texte est découpé jusqu'à
  la fiche suivante. Les champs sont lus avec les libellés du document.
- **Tableaux** : lus par les libellés de colonnes de la carte, y compris les en-têtes sur
  deux lignes et les suites de tableau sur la page suivante. Rôles reconnus :
  `synthese_actions`, `synthese_unites`, `synthese_mesures`, `parcelles`, `frise`.
- **SIG et tableur** : mêmes règles, donc un attribut `Secteur 2` est reconnu comme unité
  au même titre que `UG 2`.
- **Frises de périodicité** : toujours détectées génériquement (ligne d'années suivie d'une
  ligne `N / N+1`), en second témoin des conventions.

## Ce que la carte ne fait pas

- Elle ne produit **aucun fait métier**. Les actions, UG, surfaces et dates sortent toujours
  des extracteurs déterministes, avec leur source précise.
- Elle ne décide **aucune question**. Les questions posées au BE viennent des règles fixes de
  la réconciliation.
- Elle n'écrase rien : les faits issus de la carte (familles, conventions) sont marqués
  `methode="llm"` avec une confiance moindre, et se confrontent aux faits déterministes.

## Coût et cache

- Un appel par plan de gestion, **mis en cache** sous `{sha256}.carte.json`. Un document
  déjà cartographié n'est jamais relu.
- Une carte peut être injectée sans appel : `executer(chemins, cartes={nom: carte})`.
  C'est la base du futur **profil par bureau d'études** : une carte validée une fois sert aux
  dossiers suivants du même BE, dont les gabarits sont très stables.

## Comment le lire après un run

Dans le JSON de sortie :

```json
"cartes": { "plan.pdf": { "vocabulaire": {...}, "nb_fiches": 13, "nb_tableaux": 6,
                          "verification": {"fiches": [13, 13], "tableaux": [5, 6],
                                           "ecartes": ["tableau parcelles p.44 (en-têtes introuvables)"]},
                          "remarques": [...] } },
"regles": { "source": "carte", "unite": "...", "code": "...", "champs": {...} }
```

- `regles.source` vaut `carte` ou `defaut` : c'est le premier réflexe de diagnostic.
- Beaucoup d'écarts dans `verification` = le LLM reformule au lieu de citer. Il faut durcir
  le prompt ou changer de modèle.
- `selection_pdf[...].fiches_localisees` (« 13/13 ») indique si les fiches annoncées ont
  toutes été retrouvées dans le texte.

## Tests

`tests/test_carte.py` couvre trois situations :

1. **Le Barp avec une carte volontairement polluée** (une fiche et un tableau inventés) :
   les deux sont écartés, le reste est extrait normalement.
2. **Un plan « CEN » au vocabulaire différent, sans carte** : aucun lien action ↔ unité
   n'est trouvé. C'est la démonstration de la suradaptation.
3. **Le même plan avec sa carte** : fiches et tableau concordent, les libellés des secteurs
   et l'année d'état zéro sont extraits, la réconciliation donne trois actions en confiance
   haute.