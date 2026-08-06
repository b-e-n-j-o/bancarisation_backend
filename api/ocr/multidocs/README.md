# `api/ocr/multidocs` — pipeline multi-documents d'un dossier BE

Analyse d'un dossier de compensation écologique déposé par un bureau d'études :
plusieurs fichiers hétérogènes (PDF, XLSX, DOCX…) → **claims** typés, ancrés,
jamais réconciliés. La réconciliation, la génération de calendrier, le patch et
le rejeu du réalisé sont des étages **à venir**, hors de ce package pour l'instant.

```bash
# depuis backend/
python3 -m api.ocr.multidocs.run dossier_BE/ --jusqu-a triage      # cartographie seule
python3 -m api.ocr.multidocs.run dossier_BE/ --out analyse_out/    # + plan
python3 -m api.ocr.multidocs.run dossier_BE/ --executer            # + claims
python3 -m api.ocr.multidocs.run dossier_BE/ --catalogue           # registre
```

Imports relatifs vers l'existant (`..models`, `..mistral_client`, `..extractions.*`) :
rien à modifier dans les briques OCR déjà en place — on les **réutilise** via des
adaptateurs dans `extracteurs/`.

---

## Idée produit en une phrase

Le mauvais axe, c'est « type de fichier » (PDF / Excel). Le bon axe, c'est
**ce que le document a le droit d'écrire dans le modèle métier**. Quatre familles,
quatre destinations, quatre interdits — et un orchestrateur qui produit non pas
« un calendrier », mais **un calendrier + un rejeu de l'historique dessus**
(deux opérations distinctes, deux agents distincts plus tard).

| Famille | Exemples | Destination | Interdit |
|---|---|---|---|
| **Sources de règles** | plan de gestion, fiches-actions, calendrier prévisionnel | `action` + `echeance_regle` → semoir | — |
| **Sources d'obligations** | arrêté, convention/ORE, autorisation | `prescription` (checklist) | **jamais** créer d'occurrence |
| **Sources de coût** | estimation, devis, marché, planning € | `ligne_budget` → rattachement | jamais créer une échéance seul |
| **Sources de réalisé** | décomptes, factures, CR, onglet PGZHC | statuts + montants engagé/réalisé | jamais toucher aux règles |

Décisions produit déjà tranchées :
- l'arrêté **n'est pas** une 2ᵉ source de calendrier (juillet) ;
- le cas normal **n'est pas** le cold start — un Excel type Le Porge contient déjà
  décomptes et réalisé/projeté/supprimé : il faudra un **rejoueur d'état**, pas
  seulement un générateur ;
- fusionner « patcheur de règles » et « rejoueur d'état » dans un même agent LLM
  garantit de voir une récurrence décalée parce qu'une facture 2024 était en retard.

Le fichier à relire en premier : `utils/familles.py`. C'est lui qui empêche les
bugs qu'on ne voit qu'à six mois chez le client.

---

## Architecture globale (cible)

Orchestration **déterministe** (DAG fixe). Le LLM décide *à l'intérieur* des
étages, jamais en ReAct libre sur 70 pages réglementaires — le mode d'échec
sinon, c'est la couverture partielle silencieuse.

```
dossier BE
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  0 · Normalisation          ✅ implémenté                   │
│      fichier → blocs markdown + ancres adressables          │
├─────────────────────────────────────────────────────────────┤
│  1 · Triage                 ✅ implémenté                   │
│      LLM : rôles × plages, paramètres (T0…), signaux        │
├─────────────────────────────────────────────────────────────┤
│  2 · Plan d'extraction      ✅ implémenté                   │
│      déterministe : carte + registre → jobs ordonnés        │
├─────────────────────────────────────────────────────────────┤
│  3 · Extraction → claims    ✅ implémenté (partiel)         │
│      extracteurs versionnés, affirmations ancrées           │
├─────────────────────────────────────────────────────────────┤
│  4 · Réconciliation         ❌ à venir                      │
│      blocking déterministe + arbitre LLM sur ambiguïtés     │
├─────────────────────────────────────────────────────────────┤
│  5 · Génération (semoir)    ❌ à venir (déjà partiel hors   │
│      occurrences.py = seul à matérialiser des dates           │
│      package)                                               │
├─────────────────────────────────────────────────────────────┤
│  6 · Patcheur / rejoueur    ❌ à venir                      │
│      ops typées sur règles ≠ rejeu de réalisé               │
├─────────────────────────────────────────────────────────────┤
│  7 · Audit de couverture    ❌ à venir                      │
│      résidu non expliqué → passe LLM ciblée                 │
└─────────────────────────────────────────────────────────────┘
```

Principe transversal, généralisé depuis le prompt budget
(« NE RÉCONCILIE JAMAIS LES TABLES ENTRE ELLES ») :

> **Un extracteur affirme, il ne réconcilie pas.**
> Deux tables décrivant la même prestation → deux claims, pas une fusion.
> Le rapprochement est un étage séparé, avec hiérarchie d'autorité et conflits
> remontés à l'UI — jamais résolus en silence.

Le LLM **ne choisit jamais un extracteur par son nom**. Il cartographie
(compréhension du dossier) ; l'appariement rôle × format → extracteur est une
**jointure** contre `utils/registre.py`. La partie agentique est en amont, pas
dans le routage.

---

## Structure du package (état actuel)

```
multidocs/
├── run.py                      orchestration bout en bout
├── etape1_normalisation.py     ① dossier → Corpus
├── etape2_triage.py            ② LLM → CarteDossier
├── etape3_planification.py     ③ carte + registre → PlanExtraction
├── etape4_extraction.py        ④ exécution des jobs → claims.jsonl
├── extracteurs/                adaptateurs des protos existants
│   ├── budget_xlsx.py          → Kind.ligne_budget (fan-out par table)
│   └── plan_gestion.py         → Kind.action / Kind.echeance_regle
└── utils/
    ├── corpus.py               contrat bloc / ancre
    ├── claims.py               contrat de sortie commun (Claim)
    ├── familles.py             rôles → familles → droits d'écriture
    ├── registre.py             catalogue déclaratif d'extracteurs
    ├── plan.py                 Job / Contexte / PlanExtraction
    ├── pdf.py · tableur.py · texte.py   normaliseurs de format
```

Sorties rejouables / diffables :

| Fichier | Contenu |
|---|---|
| `corpus.json` | documents normalisés + blocs ancrés |
| `carte.json` | rôles, segments, paramètres, signaux |
| `plan.json` | jobs planifiés + `non_couvert` + avertissements |
| `claims.jsonl` | un claim par ligne, avec ancre et provenance |

---

## Étape 0 — Dépôt SIG (amont)

Avant l'analyse documentaire, l'utilisateur dépose ses fichiers SIG. L'API
`POST /projets/{id}/sig/analyse` normalise les couches (`utils/sig.py`), propose
les catégories ERC depuis le nom de fichier, et retourne un `AnalyseSig` **sans
écrire** dans `bancarisation.*`. La confirmation
`POST /projets/{id}/sig/confirmer` persiste **une ligne par géométrie** via
`persister_ug`, toutes rattachées au même `ug_id` (un .shp = une UG multi-parcelles).
La table attributaire de chaque feature est conservée dans `attributs` jsonb.

Les zones confirmées sont injectées dans `Contexte.zones_sig` → prompts des
extracteurs de règles (`zone_source_proposee`).

Fichiers clés : `utils/sig.py`, `extracteurs/sig_ug.py`,
`api/projets/geometries/sig_analyse.py`, migrations `005_sig_zones_alias.sql`,
`006_attributs_source_ug.sql` et `007_cadastre_autour_ug.sql`.

À la confirmation SIG, le module `api/cadastre` interroge l'API Carto IGN
(buffer ~100 m par UG), stocke les parcelles, et la carto les affiche en traits.
---

Tout fichier devient une liste de **blocs adressables**. L'ancre
`doc_id#locator` est le seul identifiant qui circule ensuite. Sans
« cette échéance vient de la p.34 », le BE ne peut pas vérifier — et sans
vérifiable, l'outil ne vaut rien devant un instructeur.

| Format | Grain du bloc | Locator |
|---|---|---|
| PDF | une page (ce que le chargé BE sait vérifier) | `p34` |
| XLSX | une **table logique** (bande de lignes non vides) | `Planning!8:40` |
| DOCX / MD | une section (découpe sur les titres) | `§7` |

Le markdown envoyé aux extracteurs porte les ancres inline (`⟦p34⟧`) : le modèle
peut citer sa source, l'UI la résoudre.

Points qui paient tout de suite :

- **`utils/tableur.py` remplace la passe 1 de `extract_budget.py`.** Découpage en
  tables, cellules fusionnées (`⟨montants communs aux lignes 3-4⟩`), marqueurs
  `[TOTAL?]` : produits par `openpyxl` en **double passe** (valeurs + formules) —
  exact, gratuit, non hallucinable. Testé : classeur à deux tables empilées dans
  la même feuille → découpage et annotations corrects.
- **`ocr_pages` est injectable** dans `normaliser_pdf` : brancher la fonction
  existante, ne pas la réécrire. Cache OCR par `doc_id` (donc par contenu /
  sha256) — rejouer l'analyse ne doit jamais re-payer l'OCR.
- **`normaliser_dossier()`** remonte aussi les fichiers **non traités**
  (shapefile zippé, `.dwg`, échec de parsing). Un trou de couverture silencieux
  est le pire mode d'échec : il doit être visible.

Ancrage temporel (contrat déjà dans `ParametreDossier`) : beaucoup de plans
expriment du relatif (« N+2 », « pendant 30 ans à compter de la signature »).
On stocke l'offset **et** la référence T0 ; on résout à la génération. Si le T0
bouge, tout le calendrier se recale sans réextraction.

---

## Étape 2 — Triage ✅

Le LLM ne voit que des **aperçus tronqués** et ne répond qu'à trois questions :

1. quels **rôles** sur quelles **plages** de blocs ?
2. quels **paramètres** du dossier (`annee_t0`, `origine_t0`, `duree_ans`, …) ?
3. quels **signaux** (doublon, contradiction, manque) ?

Il n'extrait **aucune** action, aucun montant. Un même fichier peut porter
plusieurs rôles : un plan de gestion contient souvent calendrier récap +
estimation en annexe → 1 fichier, 3 segments, 3 extracteurs. Un classeur type
Le Porge peut cumuler `estimation_budget` / `planning_financier` /
`decompte_facturation` / `statut_realisation`.

Garde-fou : `valider_carte()` écarte tout segment pointant vers un `doc_id` ou
un locator inexistant. Le LLM n'a pas le droit d'inventer une adresse.

---

## Étape 3 — Planification ✅

100 % déterministe. Le LLM a déjà fait ce qu'il fait bien (reconnaître un
document) ; l'appariement rôle → extracteur est une jointure, pas un jugement.

- Jobs ordonnés par famille :
  `contexte → règles → coût → obligations → réalisé`
  (donc le budget reçoit le référentiel de codes mesure déjà extrait, sans
  qu'aucun extracteur n'ait eu à réconcilier).
- `non_couvert` = segments cartographiés qu'**aucun** extracteur ne sait
  traiter. C'est un livrable en soi : le backlog mesuré pour couvrir un nouveau BE.
- Confiance sous seuil → avertissement « à faire valider avant extraction ».

Objets dans `utils/plan.py` :

- **`Job`** = `(extracteur, document, plage de blocs)` — unité rejouable,
  cachable, mesurable.
- **`Contexte`** = ce qu'on injecte en plus du texte (T0, UG, référentiel
  d'actions, claims amont, compteur de coût).

---

## Étape 4 — Extraction → claims ✅ (couverture partielle)

Chaque extracteur déclare dans le registre : `roles`, `formats`, `produit`,
`cout`, éventuellement `fan_out`. Prendre en charge un nouveau BE = **enregistrer
un extracteur**, jamais toucher à l'orchestrateur.

Un `Claim` porte toujours quatre choses (`utils/claims.py`) :

| Champ | Rôle |
|---|---|
| `doc_id` + `ancres` | d'où ça vient → vérifiable par le chargé BE |
| `extracteur` + `version` | qui l'a produit → rejouable, mesurable en éval |
| `confiance` + `champs_a_confirmer` | à quel point → priorisation de revue |
| `kind` + `donnees` | quoi → payload validé Pydantic |

Kinds prévus : `parametre_dossier`, `unite_gestion`, `action`, `echeance_regle`,
`occurrence_datee`, `ligne_budget`, `prescription`, `fait_realise`,
`reference_croisee`.

### Extracteurs branchés aujourd'hui

| Extracteur | Rôles | Sortie | Note |
|---|---|---|---|
| `plan_gestion` | plan_gestion, fiches_actions | `action` + `echeance_regle` | distributeur (bornes+échéances) puis scribes légers |
| `budget_table_xlsx` | estimation_budget, planning_financier, devis_marche | `ligne_budget` | **une table à la fois** |

**Changement notable budget :** la passe 1 LLM de cartographie du classeur
disparaît (`tableur.py` l'a déjà faite). Extraction table par table = correction
directe du test à **>32k tokens de réflexion** : plus de contradiction
inter-tables à arbitrer dans le prompt, donc plus de boucle. Les doublons voulus
sortent en deux claims distincts.

### Extracteurs manquants (backlog = `non_couvert` au premier run)

Listés dans `extracteurs/__init__.py` :

- `prescriptions_arrete` → `Kind.prescription`
- `statut_realisation_xlsx` → `Kind.fait_realise`
- `decompte_xlsx` → `Kind.fait_realise`
- `calendrier_recap` — **contrôle croisé**, ne produit **pas** d'échéance :
  extrait le récap, déroule les règles, compare ; un écart = signal de qualité
- `ug_table` → `Kind.unite_gestion`

---

## Étapes suivantes (hors package pour l'instant)

### 5 · Réconciliation + résolution d'entités

Blocking déterministe d'abord (code mesure > UG > similarité libellé/embedding),
puis LLM **uniquement** sur les paires ambiguës, avec droit explicite de répondre
« non ». Jamais de LLM sur le produit cartésien.

Hiérarchie d'autorité en conflit :
`arrêté > convention > plan de gestion en vigueur > devis > CR`.
Un conflit est un **objet de premier ordre** remonté à l'UI.

Sur Le Porge, le code mesure est une clé quasi-parfaite : s'en servir pour
calibrer le blocking.

### 6 · Génération (semoir)

`occurrences.py` (déjà ailleurs dans le backend) reste le **seul** à matérialiser
des dates. Le LLM n'émet jamais une liste de dates pour du récurrent : il émet
une **règle**. La séparation semoir / vue tient : la défendre à chaque étage.

### 7 · Patcheur de règles + rejoueur de réalisé

Deux agents, pas un.

Le patcheur n'écrit pas le calendrier : il émet un **diff d'opérations typées**,
appliqué par du code :

`ADD_ECHEANCE` · `UPDATE_RECURRENCE` · `SHIFT_ANCRAGE` · `SPLIT_ECHEANCE` ·
`SUPERSEDE` · `ATTACH_BUDGET` · `SET_STATUT_OCCURRENCE` (réservé au rejoueur)

Chaque op : justification + provenance + confiance. Auditable, réversible,
testable. Le patch porte sur la **règle**, pas sur les occurrences — sinon perte
de cohérence à la régénération. Une exception ponctuelle (« ce passage 2027 est
décalé à 2028 ») est un *override sur occurrence*, objet différent.

Politique de reprise (prolongement du garde-fou `origine='ia' AND modifie_le IS NULL`) :

| État | Traitement |
|---|---|
| passé / réalisé | **gelé** — jamais touché, seulement annoté |
| futur, `origine='ia'`, non modifié | **régénérable** |
| futur, modifié par l'utilisateur | **conflit** → file de revue |

C'est ce qui rend tenable « plan de gestion révisé 5 ans plus tard ».

Le **rejoueur** (décomptes / PGZHC) rejoue l'historique sur le calendrier : c'est
lui qui rend l'outil crédible sur les projets déjà en cours.

### 8 · Audit de couverture (« exhaustif »)

Relire tout le corpus une 2ᵉ fois ne marche pas. Calculer le **résidu non
expliqué** par du code, n'envoyer *que lui* au LLM :

- pages sans aucun claim ;
- nombres de tableaux non repris en ligne budget ;
- codes mesure cités absents du référentiel ;
- années hors horizon généré ;
- écarts de sommes (`controle_croise`) ;
- fiches-actions sans échéance ; échéances sans ancrage.

Question LLM sur ce seul résidu : « qu'est-ce qui aurait dû être extrait ici ? ».

Côté UX : on ne fait pas valider 400 occurrences, on fait valider **~20 règles**,
triées par impact (occurrences affectées × budget déplacé). Une règle validée
effondre 15 lignes.

---

## Dettes assumées (dans le code actuel)

1. **Ancres dans les contrats d'extraction.** `Echeance` et `Action` n'ont pas
   de champ `ancres`. On retombe sur les ancres du job (justes mais grossières)
   ou sur `pages` quand il existe. Ajouter `ancres: list[str]` aux modèles + une
   ligne de prompt (« reprends l'adresse ⟦…⟧ du bloc d'origine ») : ~10 lignes,
   et la vérif page à page devient possible dans l'UI.
2. **Fan-out du plan de gestion.** Les deux extracteurs PDF avalent encore le
   document entier (coût ×N, dilution long-contexte, provenance floue).
   `segmenter_fiches()` dans `plan_gestion.py` est amorcé (regroupement des
   pages par titre portant un code mesure) mais **pas branché**. Cible :
   1 passe de segmentation cheap (`effort=none`, schéma strict) → fan-out
   1 appel par fiche (+ en-tête de contexte partagé) → consolidation. Basculer
   quand un second plan de gestion permettra de vérifier que la segmentation
   généralise — sinon remplacer l'heuristique par une passe LLM de bornes.
3. **Extracteurs manquants** — voir liste plus haut ; le contenu de
   `non_couvert` au premier run est le backlog réel.
4. **Golden set.** Un seul dossier = surajustement. Figer markdown OCR + JSON
   attendu corrigé à la main ; mesurer rappel des actions / justesse du type de
   récurrence / justesse de l'ancrage / F1 des occurrences par année. L'UI de
   vérification-correction *est* l'outil d'annotation : instrumenter
   avant/après/source dès la première mise en main.

---

## Ordre de chantier (rappel)

1. ~~Normalisation + ancres~~ ✅ (+ rétro-adaptation des protos PDF via adaptateurs)
2. ~~Registre + contrat Claim~~ ✅ ; convertir / brancher les extracteurs existants
3. ~~Triage + plan d'extraction~~ ✅
4. Étage de réconciliation (blocking d'abord, arbitre LLM ensuite)
5. Patcheur ops typées + politique de reprise
6. Rejoueur de réalisé (décomptes / PGZHC)
7. Audit de couverture
8. En parallèle dès maintenant : golden set + instrumentation des corrections UI

---

## Premier run utile — Testemaure + Le Porge

```bash
python3 -m api.ocr.multidocs.run dossier_Testemaure_LePorge/ --jusqu-a triage --out analyse_out/
# puis, si la carte est saine :
python3 -m api.ocr.multidocs.run dossier_Testemaure_LePorge/ --executer --out analyse_out/
```

À regarder en priorité :

1. le triage attribue-t-il bien **plusieurs rôles** au classeur Le Porge
   (`estimation_budget` / `planning_financier` / `decompte_facturation` /
   `statut_realisation`) ?
2. `annee_t0` et `origine_t0` sont-ils détectés, et avec quelle ancre ?
3. contenu de **`non_couvert`** — backlog d'extracteurs **mesuré**, pas supposé ;
4. nombre de blocs par table du classeur : si `_bandes()` sur-découpe une matrice
   actions × années, ajuster `MAX_LIGNES_TABLE` ou tolérer une ligne vide interne
   avant de couper ;
5. le budget sort-il bien **une table à la fois**, sans boucle de réflexion ?
