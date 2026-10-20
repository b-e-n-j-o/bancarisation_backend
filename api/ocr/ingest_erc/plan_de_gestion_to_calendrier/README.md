# pgcal — du plan de gestion au calendrier d'interventions

Prototype dédié **au plan de gestion uniquement**. Objectif : produire le calendrier
pluriannuel le plus juste possible, avec pour chaque intervention une **confiance**
qui décide si elle est intégrée directement ou proposée à l'utilisateur.
Géométries et budget sont hors périmètre.

## Principes

1. **Le texte entier en contexte.** Mistral Large 4 accepte 1M de tokens : un plan de gestion
   (40 à 150 pages) passe d'un bloc. On ne découpe pas, pour éviter les contresens hors contexte,
   comme lire « 5 ans » au lieu de 30.
2. **Le LLM interprète, le code calcule.** Le modèle produit des *règles* (segments d'années,
   pas, passages, mois, déclencheurs). `s6_expand` les déroule en occurrences. Le LLM ne compte jamais.
3. **Les grilles sont lues à part, et deux fois.** Les cases cochées, les bandes fusionnées et les
   **mois codés uniquement par la couleur des cellules** sont lus géométriquement dans le PDF
   (pdfplumber : cellules, mots, rectangles remplis), puis relus par le modèle vision. Le LLM reçoit
   une transcription propre des grilles en plus du texte.
4. **Chaque affirmation est vérifiée.** Citations retrouvées dans la page, années confrontées aux
   grilles (colonne par colonne et cadence « tous les k ans »), horizon du cadre confronté aux grilles,
   accord entre plusieurs échantillons du LLM. Les actions à problème repassent en relecture critique.
5. **Tout est tracé.** Chaque étape écrit sa sortie dans le dossier du run.

## Arborescence : une étape, un fichier

| Fichier | Étape | Rôle |
|---|---|---|
| `pgcal/config.py` | — | Tous les réglages : modèles, seuils, nombre d'échantillons, politiques |
| `pgcal/schemas.py` | — | Tous les modèles de données (ceux demandés au LLM et ceux échangés entre étapes) |
| `pgcal/llm.py` | — | Client Mistral unique : chat JSON (schéma), vision, OCR, cache disque, journal |
| `pgcal/prompts.py` | — | Tous les prompts |
| `pgcal/s1_ingest.py` | 1 | PDF → texte par page (natif « layout », sinon OCR), numéro imprimé, rendu PNG |
| `pgcal/s2_locate.py` | 2 | Pages du plan, rôle de chaque page, code de fiche (y compris les pages de suite), codes candidats, indices de durée et de N |
| `pgcal/s3_grids.py` | 3 | Lecture des grilles annuelles et mensuelles : géométrie + couleurs + vision, réconciliation |
| `pgcal/s4_extract.py` | 4 | Cadre (durée, N, UG, actions), puis règles par action en k échantillons, consolidation |
| `pgcal/s5_verify.py` | 5 | Vérifications, relecture critique, score de confiance, décision |
| `pgcal/s6_expand.py` | 6 | Déroulement déterministe des règles en occurrences datées |
| `pgcal/s7_export.py` | 7 | `calendar.json`, `occurrences.csv`, `review.md` |
| `pgcal/pipeline.py` | — | Enchaîne les 7 étapes et écrit les artefacts |
| `pgcal/__main__.py` | — | Ligne de commande |
| `eval/evaluate.py` | — | Compare un calendrier à un jeu de référence |
| `eval/gold/bruges_rules.json` | — | Référence pour Bruges : cadre + 18 règles avec citations |
| `eval/gold/bruges_occurrences.csv` | — | Référence pour Bruges déroulée (135 lignes) |
| `tests/` | — | Tests hors ligne : grilles, déroulement, détection d'erreurs, plomberie LLM simulée |

## Installation et utilisation

```bash
pip install -r requirements.txt          # + poppler-utils conseillé (pdftotext)
cp .env.example .env && export $(cat .env | xargs)

python -m pgcal run plan.pdf                       # pipeline complet
python -m pgcal run plan.pdf --n-year 2026         # recale N sur l'année réelle des travaux
python -m pgcal run plan.pdf -k 5 --step-policy min
python -m pgcal grids plan.pdf --pages 24,25,30    # étape 3 seule, sans API : utile pour déboguer les grilles

# hors ligne : injecter des règles (gold, ou sortie de votre pipeline actuel) pour tester étapes 5 à 7
python -m pgcal run plan.pdf --offline --rules-json eval/gold/bruges_rules.json
python eval/evaluate.py runs/<run>/calendar.json eval/gold/bruges_rules.json

PGCAL_TEST_PDF=/chemin/plan.pdf pytest -q tests/
```

Les identifiants de modèles sont dans `.env`. `mistral-large-4-0` est documenté. Pour OCR 4.1,
`mistral-ocr-4-1` est l'identifiant probable : à confirmer dans la console Mistral.

## Contenu d'un run (`runs/<doc>_<horodatage>/`)

`01_document.json` → `02_map.json` → `03_grids.json` / `03_grids.txt` → `04_frame.json`,
`04_samples.json` (échantillons bruts) → `05_rules.json` (règles + détail du score) →
`calendar.json`, `occurrences.csv`, `review.md`, `llm_calls.jsonl` (modèle, durée, tokens, cache).

Les appels LLM sont mis en cache : relancer un run ne coûte rien, et `--offline` rejoue le cache.

## Confiance et décision

Score d'une règle = moyenne pondérée de :

| Composante | Poids | Mesure |
|---|---|---|
| citations | 0,30 | Part des extraits retrouvés dans la page citée (±1 page) |
| accord échantillons | 0,25 | Présence dans les k échantillons × part d'échantillons identiques (années, mois) |
| grille | 0,25 | Cohérence avec les croix, les bandes et leur cadence, et avec les mois colorés (poids redistribué s'il n'y a pas de grille) |
| précision | 0,10 | Pénalités : fourchette (« 2 à 3 ans »), « une dizaine de visites », mois déduits de la grille, ambiguïtés |
| auto-évaluation | 0,10 | Confiance déclarée par le modèle |

Décisions :
- **intégré** si le score est ≥ 0,80 et qu'il n'y a aucune alerte ;
- **à valider** si le score est ≥ 0,55 ;
- **à revoir** en dessous ;
- **déclencheur** pour une règle conditionnelle (pas de date) ;
- **à préciser** pour une récurrence sans fréquence.

Une occurrence issue d'une hypothèse (fourchette) ou d'un passage optionnel est plafonnée à « à valider ».
Les seuils se règlent dans `config.py` : il faut les caler sur le jeu d'évaluation (section `CALIB.` d'`evaluate.py`).

## Cas couverts (au-delà de Bruges)

- grilles en années relatives (N, N+k), absolues (2025…) ou par plages (« N+6 à N+10 », « 2041à 2046 ») ;
  en-têtes sur deux lignes ou cellules d'en-tête coupées ; tableau réparti sur plusieurs pages ;
- croix, bandes fusionnées avec texte, cellules seulement colorées ; grilles mensuelles codées par la couleur ;
- fiches sur plusieurs pages (le code de la fiche est hérité) ; libellés verticaux ignorés ;
- coquilles de code (RG3 pour RG2) repérées par le même intitulé ; actions présentes dans les fiches ou
  les grilles mais oubliées par le cadre, ajoutées ;
- fréquences : années explicites, annuelle sur une plage, tous les k ou k1–k2 ans, n passages par an
  dont certains optionnels, « une dizaine sur la durée », activité continue, conditionnelle,
  non précisée, ancrage sur un autre événement que N (signalé) ;
- PDF scannés : OCR des pages sans texte, grilles lues par la vision seule ;
- plan inclus dans un dossier plus large (DDEP) : pages hors plan écartées à l'étape 2.

## Limites connues

- L'appariement règle ↔ ligne de grille repose sur une similarité de libellés (racinisation légère,
  mots vides). Quand plusieurs lectures sont plausibles, la plus cohérente est retenue. C'est indulgent
  par construction, et la relecture critique sert de filet de sécurité.
- Une sous-action entièrement oubliée par les k échantillons n'est rattrapée que par la relecture
  critique. La couverture déterministe ne fonctionne qu'au niveau des codes d'action.
- Les règles ancrées sur un autre événement que N sont signalées mais déroulées depuis N.
- Les grilles de PDF scannés ne sont lues qu'une fois (par la vision) : la confiance « grille » y est moins solide.
- Un seul jeu de référence pour l'instant. Il faut 4 ou 5 plans annotés, de bureaux d'études
  différents, pour régler les poids et les seuils.

## Coût indicatif (plan de 50 pages)

Environ 4 appels de classification, 1 appel vision par page de grille, 1 appel cadre, 1 appel par
action (k échantillons via `n`), et des relectures sur les seules actions à problème. Soit environ 25 appels
de 40 à 60k tokens en entrée chacun, avec un préfixe identique mis en cache (`prompt_cache_key`).
