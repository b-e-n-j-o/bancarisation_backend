# Le pipeline d'ingestion

> La cartographie est documentée à part (`CARTOGRAPHIE.md`). Ce document décrit tout le reste :
> de l'arrivée des fichiers au référentiel verrouillé, puis ce qui est prévu pour la suite.
>
> **État** : les étapes 0 à 2 sont implémentées et testées. L'étape 2.5 est partiellement
> implémentée (écran + corrections côté UI ; réinjection à finir). La passe 3 est une
> conception, pas du code.

## Vue d'ensemble

| Étape | Rôle | Nature | État |
|---|---|---|---|
| 0 | Inventaire du dépôt | déterministe | fait |
| 0 bis | Texte des PDF (pdftotext + OCR) | appel externe en cache | fait |
| 1 | Carte du document | 1 appel LLM, vérifié | fait |
| 1a | Profil SIG | déterministe | fait |
| 1b | Faits de référentiel | déterministe (+ LLM optionnel) | fait |
| 2 | Réconciliation | déterministe | fait |
| 2.5 | Validation par le bureau d'études | humain | en cours |
| 3 | Extraction lourde (calendrier, budget) | déterministe + LLM | à faire |
| 4 | Changeset et application | déterministe + humain | à faire |

Principe constant : **tout ce qui sort d'un extracteur est un `Fait`** — une affirmation, sa
source (document, page ou cellule), sa méthode (`deterministe`, `llm`, `user`) et une
confiance. Rien n'est écrit en base sans passer par la réconciliation puis par un humain.

---

## Étape 0 — Inventaire

`inventaire.inventorier(chemins)`

- **Empreinte** sha256 de chaque fichier : sert au cache OCR et à repérer les doublons stricts.
- **Rôle du document** : `arrete`, `plan_gestion`, `tableur`, `sig`, `execution`, `autre`.
  Le rôle est déduit de motifs en tête de document ; en cas de motifs concurrents, **celui
  qui apparaît le plus tôt gagne** (le sommaire d'un plan cite des arrêtés).
- **En-têtes et pieds répétés** : les lignes présentes sur plus de la moitié des pages sont
  retirées du texte et conservées à part (`lignes_repetees`). Le pied sert de témoin pour le
  nom du projet.
- **Annexes embarquées** : repérées dans la table des matières, avec leur plage de pages et un
  rôle probable (arrêté, budget).
- **Doublons** : une annexe « arrêté » est comparée aux arrêtés déposés, par similarité de
  contenu, ou de titre si les pages sont scannées. Reconnue, elle est ignorée. Non reconnue,
  elle devient une décision à part entière (l'annexe 1 de Le Barp : arrêté de défrichement).

### Texte des PDF — `ocr.py`

Trois versions coexistent par page :

| Champ | Provenance | Usage |
|---|---|---|
| `texte_natif` | pdftotext | vérification des chiffres |
| `markdown` | Mistral OCR | tableaux, titres, pages scannées |
| `texte` | markdown normalisé (`mdnorm`) | regex |

Le mode `hybride` est le défaut dès qu'une clé Mistral est présente. Chaque page reçoit un
score `fiabilite_ocr` : la part des nombres de l'OCR retrouvés dans la couche native. En
dessous de 0,8, les valeurs numériques sont marquées douteuses et les matrices de la page ne
sont pas exploitées en passe 1.

Tout est mis en cache par sha256 : un document déjà lu n'est jamais relu.

---

## Étape 1a — Profil SIG

`sig_profil.profiler_sig(zip, regles)`

- **Noms de fichiers réparés** (encodages zip Windows/Mac), CRS lu dans le `.prj` et
  **reprojection en EPSG:2154**. Une couche sans CRS identifiable est refusée.
- **Profilage des colonnes** : chaque colonne est typée — identifiant d'unité, référence
  cadastrale, surface, années, autre nomenclature. Une colonne nommée comme une unité mais
  dont les valeurs ne suivent pas la nomenclature du plan est ignorée avec un avertissement
  (à Le Barp, la colonne `UG` contient les unités de l'aménagement forestier : `09.04b`).
- **Zones candidates** : une zone = une couche, ou une couche filtrée par une valeur
  d'attribut d'unité. Chaque zone porte son nombre d'entités, sa surface, ses références
  cadastrales et son type ERC déduit du nom de couche.

---

## Étape 1b — Faits de référentiel

Trois familles d'extracteurs, tous producteurs de `Fait`.

### Tableur — `excel_profil.py`, `extract_referentiel.faits_tableur`

Trois couches, inspectables séparément :

1. **Grille** : fusions dépliées, couleurs, formules, type de chaque cellule.
2. **Blocs** : détectés par leur ligne d'années et leur ligne de rangs (`N`, `N+5`,
   `Etat zéro`), avec leurs colonnes identifiantes (commune, section, numéro, surface, code),
   leur titre, leurs lignes de données et de totaux, et leurs suites.
3. **Enregistrements** : une ligne par cellule utile, rattachée au groupe de parcelles couvert
   par la fusion.

Sortent aussi : le tableau de synthèse code ↔ unité, les légendes d'abréviations, les ancres
temporelles de chaque bloc, et le coefficient entre lignes et totaux (1,1 à Le Barp, soit une
TVA implicite).

### Plan de gestion — `pdf_sections.py`, `extract_referentiel.faits_plan`

Avec une carte, les fiches et les tableaux sont lus aux emplacements et avec les libellés
qu'elle donne. Sans carte, le repli heuristique s'applique : familles de codes apprises dans
le document, sections scorées, sélection sous budget de caractères.

Faits produits : intitulé, unités, parcelles et objectif de chaque action, familles de codes,
conventions temporelles, et — par les tableaux — synthèses actions, unités, mesures, listes de
parcelles.

### Arrêté — `extract_referentiel.faits_arrete`

Entièrement par regex, sur un texte très normé : référence et date de décision, bénéficiaire,
commune, type de procédure, durée et horizon, obligations surfaciques par mesure, surfaces
évitées. La même fonction traite les décisions embarquées en annexe, avec une clé distincte
pour ne pas écraser les champs du projet.

### LLM (optionnel)

`llm.extraire_referentiel` reçoit les sections retenues et renvoie actions, unités et années.
Ses faits s'**ajoutent** aux autres, avec `methode="llm"` et une confiance moindre. Ils ne
remplacent jamais un fait déterministe : ils servent de seconde source.

---

## Étape 2 — Réconciliation

`reconcile_ref.reconcilier(faits, zones, couches, docs)`

### Confiance

| Niveau | Condition |
|---|---|
| haute | au moins deux **sources indépendantes** concordent |
| moyenne | une seule source explicite, ou une inférence cohérente |
| basse | conflit, cellule fusionnée, ou valeur douteuse |

Une source indépendante = document + nature de l'emplacement + méthode. Une fiche et un
tableau de synthèse du même PDF comptent donc pour deux.

### Résolution des unités ↔ géométries

1. **Attribut explicite** dans la couche : rapprochement direct, confirmé par le taux de
   parcelles retrouvées dans les documents.
2. **Sinon, inférence scorée** sur quatre signaux : recouvrement des références cadastrales,
   proximité des surfaces attendues, mots communs entre nom de couche et libellé ou cible de
   l'unité, cohérence du type ERC. Toute inférence produit une question.
3. **Couche non rattachée** : question, sauf si une mesure du plan l'explique (à Le Barp,
   EV-3 « non soumis à obligation de résultats » → simple information).

### Contrôles croisés

- surfaces et nombres de parcelles de l'arrêté contre le SIG, avec tolérance ;
- mesures annoncées dans le plan contre les tableaux de parcelles et le SIG ;
- surfaces par unité, SIG contre tableur ;
- cohérence des blocs du tableur (un bloc « entretien » codé avec une famille « travaux
  uniques ») ;
- références cadastrales incomplètes ou fautives, corrigées et signalées ;
- documents : doublons, annexes scannées, décisions non déposées.

### Questions

Elles sont produites par des **règles fixes**, jamais par le LLM :

| Règle | Exemple Le Barp |
|---|---|
| valeurs divergentes entre documents | état zéro 2022 ou 2023 |
| coefficient constant entre lignes et totaux | HT ou TTC |
| cellule fusionnée dans toutes les sources | unités de SE2 et MG2 |
| rapprochement unité ↔ couche par inférence | UG5, UG6, UG7 |
| couche sans unité correspondante | — |
| titre de bloc incohérent avec son code | bloc entretien codé TU1 |
| parcelle citée introuvable | C974c |
| décision embarquée non déposée | arrêté de défrichement |
| abréviations concurrentes pour une même opération | CR et CP |

### Sortie

Un `ReferentielPropose` : trois tables pré-remplies (projet, unités, actions) où **chaque
cellule porte sa valeur, sa confiance et ses sources**, plus les questions, les contrôles, les
obligations et des statistiques (dont la part de champs GéoMCE socle renseignés).

---

## Étape 2.5 — Validation par le bureau d'études

Écran unique : questions à gauche, tables à droite, sources au survol.

- Le BE **répond** aux questions, **corrige** toute cellule, **ajoute ou retire** une unité ou
  une action. Les compteurs (« 13 extraites ») lui permettent de repérer un manque.
- Les réponses et corrections deviennent des `Fait(methode="user")`, **prioritaires**.
- **À finir** : propagation des renommages (un code d'unité modifié doit l'être partout),
  relance de `reconcilier` — instantanée et déterministe — puis **verrouillage** du
  référentiel.

Test de recette : rejouer Le Barp en acceptant les propositions doit donner zéro question,
UG1 à 13,72 ha et le bloc d'entretien requalifié en TE1.

Le référentiel verrouillé (projet, unités, actions, conventions N et HT/TTC, rapprochements
SIG) est **l'unique entrée de la passe 3**.

---

## Passe 3 — Extraction lourde *(conception)*

Elle ne démarre qu'après le verrouillage. Les clés étant figées, les extracteurs n'ont plus
à deviner : ils rattachent.

### Calendrier

- **Source principale : le tableur.** La couche 3 du profileur fournit une ligne par cellule,
  avec son code corrigé et son groupe de parcelles. Les rangs `N+x` sont convertis en années
  avec l'ancre validée.
- **Complément : les fiches et les frises du plan** pour les fenêtres d'intervention, les
  récurrences et les actions sans ligne de tableur (suivis, pilotage).
- **Priorité en cas de conflit** : tableur chiffré, puis tableur en codes, puis PDF.
- Sortie : des échéances (modèle + récurrence) passées au moteur d'occurrences.

### Budget

- Lignes appariées au calendrier par `(code, année, groupe de parcelles)`.
- Si le budget n'existe qu'au grain mesure × année et que plusieurs échéances partagent la
  clé : montant porté au niveau de l'échéance-année, ou réparti au prorata des surfaces avec
  un drapeau.
- Normalisation HT selon la convention validée ; l'annexe de coûts sert de contrôle des
  totaux, lue sur la couche native quand `fiabilite_ocr` est faible.

### Obligations et conformité

Les prescriptions de l'arrêté (échéances de transmission, fréquences de suivi, durées) sont
rapprochées des échéances produites : c'est le lien `prescription_couverture`, proposé en mode
`ia`. L'appariement est le seul endroit où un modèle à réflexion forte se justifie.

### Changeset

Le résultat n'est jamais écrit directement : c'est une liste d'opérations
(`creer_echeance`, `modifier_occurrence`, `lier_prescription`…), chacune avec ses sources, sa
confiance et ses avertissements. Revue, puis application.

---

## Projet nouveau ou déjà en cours

| | Projet neuf | Projet en cours (migration) |
|---|---|---|
| Statut des occurrences passées | `planifie` | `a_confirmer` |
| Rôle du plan | vérité | baseline (montant et année initiaux figés) |
| Documents attendus en plus | — | décomptes, CR de chantier, bilans de suivi |
| Opérations supplémentaires | — | `marquer_realise`, `decaler`, `renseigner_realise` |

Les documents anciens non structurables sont stockés, classés et indexés pour la recherche,
rattachés au projet sans extraction.

## Dépôts ultérieurs

Le même pipeline traite un document déposé des années plus tard. Seule différence : l'état de
départ n'est pas vide. Le diff se calcule contre l'état courant et respecte les garde-fous
`origine` et `modifie_le` — une occurrence modifiée par un humain reçoit une proposition,
jamais un écrasement.

## GéoMCE

Les champs nécessaires à l'export sont collectés **dès la passe 1** (identifiants de la
décision, catégorie ERC et cible par unité, géométries dissoutes). Les champs manquants
remontent comme questions dans le même écran. L'onglet d'export n'est alors qu'une projection
de données déjà propres, avec validation contre la notice.

## Points ouverts

- Réinjection complète de l'étape 2.5 et verrouillage.
- Passe 3 entière.
- Liste des champs GéoMCE à aligner sur la notice officielle en vigueur.
- Corpus de test : le pipeline n'a été éprouvé que sur des dossiers d'un même bureau d'études.
  Trois à cinq plans d'origines différentes sont nécessaires pour mesurer ce qui généralise.