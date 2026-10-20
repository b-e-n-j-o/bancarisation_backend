# pdfvisuel — lecture de l'information visuelle des plans de gestion

Documentation détaillée du traitement. Version 1.1, octobre 2026 : lecture du PDF par pdfplumber +
pypdfium2 (licences permissives), PyMuPDF retiré.

---

## 1. Le problème traité

Un plan de gestion transporte une partie de son calendrier par des canaux que la chaîne
« OCR → texte → LLM » ne voit pas :

| Canal | Exemple réel | Ce qu'en fait l'OCR |
|---|---|---|
| Couleur de fond d'une cellule | ES2 (p. 198) : avril, mai, juin surlignés, aucun mois cité dans le texte | Perdu : les cellules sont lues comme du texte « Avril Mai Juin » sans distinction des autres mois |
| Position d'une croix ou d'une bande sous une colonne | Tableau 39 : « 1 suivi tous les 5 ans » fusionné de N+6 à N+15 | Le texte flotte sans colonne de rattachement |
| Cellule fusionnée sur plusieurs années | « Fauche et griffage » de N+1 à N+30 | Début et fin de la bande perdus |
| Image collée (capture Excel, schéma) | Composition des 16 bosquets (p. 193) | Rien dans la couche texte |
| Page numérisée | — | OCR complet nécessaire |

Le principe retenu : **lire d'abord la géométrie du PDF lui-même** (traits, aplats colorés,
position des mots), sans aucun appel API, et n'envoyer en vision que les zones qui n'ont pas de
géométrie lisible (images, scans, grilles non reconstruites).

---

## 2. Vue d'ensemble

```
PDF
 │
 ├─ MODULE 1  m1_detection   (pdfplumber + pypdfium2, aucun appel externe, ~0,12 s/page)
 │    lecture de la couche vectorielle : mots, traits, aplats
 │    ├─ grilles temporelles (mois / années) reconstruites cellule par cellule
 │    ├─ images classées (document en image / photo-carte)
 │    ├─ pages scannées, zones suspectes
 │    └─ ROUTAGE : chaque zone -> lecture_vectorielle | vision_zone | ocr_page | ignorer
 │         sorties : profil.json, grilles.md, routage.md, overlays/*.png
 │
 ├─ MODULE 2  m2_vision      (API Mistral, uniquement sur les zones routées)
 │    recadrage 200 dpi -> OCR (texte/tableau en image) ou vision (grille, couleurs)
 │    cache par empreinte, mode dry-run, erreurs isolées par zone
 │         sorties : vision/vision.json, vision/zones/*.png
 │
 └─ MODULE 3  m3_assemblage  (aucun appel externe)
      texte de base (OCR existant ou couche native) + blocs <<<ENRICHISSEMENT>>> par page
      contrôles de cohérence prose / grille / libellés / zones non lues
           sorties : document_enrichi.md, controles.json, controles.md
```

Les modules 1 et 3 sont déterministes et gratuits. Le module 2 est le seul qui coûte, et il ne
voit que ce que le module 1 lui envoie : sur le plan Rivière, 3 images sur 49 pages.

---

## 3. Module 1 — détection et lecture géométrique

Fichiers : `geometrie.py`, `temporel.py`, `grilles.py`, `images.py`, `serialisation.py`,
`overlay.py`, `profil.py` (CLI).

### 3.1 Lecture de la couche vectorielle (`geometrie.py`)

Pour chaque page, pdfplumber (sur pdfminer.six) expose les objets dessinés : `page.rects`,
`page.lines`, `page.curves`, `page.chars`, `page.images`. Ils sont normalisés en trois familles
(objets `Mot`, `Trait`, `Aplat`, seuls utilisés par le reste du code) :

- **Mots** : `page.extract_words()` pour le texte droit. Les caractères **pivotés** (libellés
  verticaux « Phasage prévisionnel ») sont regroupés à part, colonne par colonne, dans le sens de
  lecture donné par leur matrice. Le drapeau `upright` de pdfminer est faux sur ce plan : on se
  fie à la matrice du caractère (|b| > |a| ⇒ pivoté).
- **Traits** (bordures de tableau), deux origines à couvrir :
  - Word et LibreOffice dessinent souvent chaque bordure comme un **rectangle rempli très fin** :
    largeur ou hauteur ≤ 1,6 pt → trait vertical ou horizontal. C'est le cas du plan Rivière
    (480 rectangles sur la page du tableau 39, aucun trait tracé) ;
  - d'autres générateurs utilisent des **segments tracés** (`page.lines`), des rectangles à
    contour, ou des **lignes brisées** (un chemin de plusieurs segments, rangé par pdfminer dans
    `page.curves`) → décomposés en segments horizontaux et verticaux.
  Les segments colinéaires qui se touchent sont fusionnés : on les regroupe d'abord par position
  (± 0,8 pt), puis on fusionne les intervalles. Ce regroupement en deux temps évite un piège réel
  rencontré page 25 : une bordure d'en-tête absorbée par une bordure voisine décalée de 0,7 pt.
- **Aplats** : rectangles remplis d'au moins 3 pt de côté (trame de cellule, bandeau coloré).
  Piège rencontré : un rectangle écrit `re h f` (rectangle + fermeture explicite) devient chez
  pdfminer une *courbe* de 6 points, pas un rectangle. Toute courbe dont les points sont tous des
  coins de sa boîte englobante est donc traitée comme un rectangle.
- **Couleurs** : pdfminer les renvoie dans l'espace d'origine (gris, RGB, CMJN, motif) ; elles
  sont converties en RGB (`couleur_rgb`). Un motif (hachures, dégradé) donne « pas de couleur ».
  Word dessine souvent deux aplats par cellule (cellule + zone intérieure) ; on retient toujours
  le plus petit aplat qui contient le point testé.

Une couleur est considérée comme **blanche** si ses trois composantes RGB sont ≥ 0,97 ; un aplat
blanc explicite (présent page 36) n'est donc jamais une marque.

### 3.2 La cellule englobante

C'est la primitive centrale. Pour un point (x, y), on cherche le trait vertical le plus proche à
gauche et à droite qui couvre y, et le trait horizontal le plus proche au-dessus et en dessous qui
couvre x. Le rectangle obtenu est la cellule, avec pour chaque côté l'information « vraiment
bordé » ou « borné par défaut ».

Conséquence utile : deux colonnes qui renvoient **la même cellule** sont fusionnées. On détecte
ainsi les bandes (« 1 suivi tous les 5 ans » sur N+6…N+15) sans détecteur de tableaux.
Un détecteur de tableaux générique (testé avec PyMuPDF dans la v1.0) a été écarté : sur les
tableaux Word, il éclate chaque colonne en 2 ou 3 sous-colonnes à cause des retraits intérieurs
des cellules.

### 3.3 Graines : repérer un en-tête temporel (`grilles.detecter_graines`)

Les mots sont regroupés en lignes visuelles (centres verticaux à moins de 0,45 × la hauteur de
ligne). Une ligne devient une **graine** si :

| Type | Condition |
|---|---|
| Mois | ≥ 6 mois distincts (noms complets ou abrégés : « Janv. », « Sept. », « Mar »…), en ordre croissant pour au moins 80 % des paires voisines |
| Initiales | la suite `JFMAMJJASOND` apparaît parmi les lettres majuscules isolées de la ligne |
| Années | ≥ 4 jetons distincts parmi les années (19xx/20xx) et les années relatives (N, N+k) |

Et dans tous les cas :

- au moins 40 à 50 % des mots de la ligne sont des jetons temporels ou des connecteurs (« à », « - ») ;
- **écart médian entre mots consécutifs ≥ 5 pt**. Dans une grille, les mots voisins sont dans des
  cellules différentes, donc espacés ; dans la prose (« de N à N+5, puis à N+10, N+20 et N+30 »,
  page 39), l'écart médian est celui d'une espace (≈ 2,5 pt). Ce critère a supprimé le seul faux
  positif rencontré sur le plan réel.

### 3.4 Axe, bandeau et colonnes (`construire_colonnes`)

1. Les graines de même type, empilées (≤ 25 pt) et alignées en x sont regroupées en un **axe** :
   la ligne « N, N+1… » et la ligne « 2025, 2026… » forment un seul axe.
2. Le **bandeau d'en-tête** va du haut de la cellule la plus haute au bas de la plus basse.
3. Les colonnes sont construites à partir de **tous les mots temporels du bandeau**, pas seulement
   de ceux de la ligne-graine. C'est indispensable pour :
   - les libellés sur plusieurs lignes : « N+16 à / N+20 / 2041 à 2045 » (page 30) ;
   - les mots coupés : « Mar / s » (page 33, fiche RG2).
4. Chaque mot donne sa cellule englobante. Si toutes les cellules sont bordées à gauche et à
   droite, couvrent tous les libellés et ne se chevauchent pas → mode `bordures`. Sinon → mode
   `milieux` : les limites sont les milieux entre libellés voisins (confiance moyenne).
5. Le libellé complet d'une colonne est le texte du bandeau dans ses limites, interprété par
   `temporel.analyser_libelle` : mois, `n_debut/n_fin`, `annee_debut/annee_fin`, `plage`.
6. Une cellule **« État zéro / État initial / T0 »** bordée et collée à gauche de la première
   colonne est rattachée à l'axe (elle ne contient aucun jeton temporel, donc aucune graine ne la
   crée).

### 3.5 Lignes de la grille (`_limites_lignes`, `detecter_grilles`)

- Limites = traits horizontaux sous le bandeau qui couvrent au moins 80 % de la plus petite
  colonne. Sans traits : intervalles des mots et des aplats de la zone temporelle (mode `texte`).
- La lecture s'arrête, dans cet ordre de priorité, quand :
  - la ligne n'a plus aucun trait vertical dans la zone (`hors_tableau` : légende, pied de page,
    titre du tableau suivant) ;
  - la ligne est un **texte pleine largeur sans couleur** : pour un axe de mois c'est la consigne
    qui suit l'en-tête (« en dehors des périodes sensibles… ») ; pour un axe d'années, la ligne est
    gardée comme bande si elle a un libellé à gauche ;
  - la ligne fait plus de 160 pt, ou l'on atteint l'en-tête d'un autre axe.
- Une ligne **vide mais bordée** est conservée sans marque (« Réouverture des fourrés » de
  N+16 à N+30, page 25) : c'est une information (« rien de programmé »).

### 3.6 Lecture des cellules et règles de marquage

Pour chaque ligne et chaque colonne : cellule englobante au centre de la ligne → segments
(colonnes consécutives qui partagent la même cellule), texte des mots contenus, couleur de
l'aplat au centre de chaque colonne (couleur majoritaire du segment).

**Règle de remplissage** (`regle_remplissage`), appliquée aux segments d'une ligne :

| Situation | Décision | Exemple |
|---|---|---|
| Cellules blanches et colorées mélangées | les colorées sont marquées (`remplissage`) | mois surlignés, Gantt |
| Tout est coloré, plusieurs couleurs | les couleurs minoritaires sont marquées (`remplissage_minoritaire`) | ligne grisée, mois actifs en vert |
| Tout est coloré d'une seule couleur | la couleur n'est pas une marque (`uniforme`) | en-tête stylé, ligne de bandeau |
| Tout uniforme ET aucun texte | `remplissage_uniforme`, signalé ambigu | ligne entièrement colorée sans libellé |

**Typage d'un segment**, par priorité : `croix` (X, x, ✓, ●, ■, « oui »…) ; `bande` (texte sur
≥ 2 colonnes) ; `texte` (texte sur 1 colonne, ex. montants d'un tableau budgétaire) ; marque de
remplissage selon la règle ci-dessus.

**En-tête auto-marqué** : la même règle est appliquée aux cellules de l'en-tête elles-mêmes.
C'est le format des fiches action du plan Rivière, où ce sont les cellules « Mars », « Avril »…
qui portent la trame, sans ligne de corps.

**Libellé de ligne** : texte des cellules situées à gauche de la zone temporelle et qui recoupent
la ligne d'au moins 50 %, lues de gauche à droite et jointes par « | ». Une cellule fusionnée
verticalement (code « RG1 » sur trois sous-lignes) est ainsi rattachée à chacune de ses
sous-lignes : « RG1 | Réouverture du milieu… | Entretien par fauche ».

### 3.7 Images (`images.py`)

Toute image de plus de 1,5 % de la page est rendue à 50 dpi et mesurée :

| Mesure | Définition |
|---|---|
| `part_blanc` | pixels dont les 3 composantes sont ≥ 232 |
| `saturation` | saturation moyenne des pixels non blancs |
| `part_encre` | pixels dont la composante maximale est ≤ 110 |
| `mots_dessus` | mots de la couche texte situés sur l'image |

| Classe | Règle | Routage |
|---|---|---|
| `vide` | part_blanc > 0,985 | ignorer |
| `texte_deja_present` | ≥ 15 mots de la couche texte sur l'image (PDF déjà OCRisé, légendes vectorielles) | ignorer |
| `document_raster` | part_blanc ≥ 0,55, saturation ≤ 0,18, part_encre ≥ 0,004 | **vision_zone** |
| `photo_ou_carte` | sinon | ignorer |

La dernière ligne de texte au-dessus de l'image (hors en-tête courant) est gardée comme
contexte (« Proposition de composition des bosquets : ») et transmise au prompt du module 2.
La priorité est haute si du vocabulaire calendaire (mois, N+k, phasage, période…) apparaît
autour de l'image.

### 3.8 Pages scannées et zones suspectes

- **Page scannée** : moins de 25 mots dans la couche texte et une image couvrant au moins 50 %
  de la page → `ocr_page`.
- **Zone suspecte** : une graine temporelle (en-tête reconnu) qu'aucune grille reconstruite ne
  couvre → `vision_zone` avec priorité haute. C'est le filet de sécurité pour les mises en forme
  que la géométrie ne sait pas lire.

### 3.9 Routage

| Action | Quand | Coût |
|---|---|---|
| `lecture_vectorielle` | grille lue en mode bordures + traits | nul |
| `lecture_vectorielle_a_verifier` | grille lue en mode milieux ou texte (confiance moyenne) | nul, vision optionnelle |
| `vision_zone` | image `document_raster`, zone suspecte | 1 appel par zone |
| `ocr_page` | page scannée | 1 appel par page |
| `ignorer` | photo, carte, image vide ou déjà textuelle | nul |

### 3.10 Sorties du module 1

- `profil.json` : pour chaque page, `grilles[]` (colonnes avec x0/x1 et interprétation, lignes
  avec segments et marques, couleurs en hexadécimal, confiance, raison de fin de lecture),
  `images[]`, `suspects[]`, `page_imprimee` ; puis `routage[]` et `synthese`.
- `grilles.md` : chaque grille en tableau Markdown (X, ■, ◀ bande ▶) **et en phrases** :
  « Période d'intervention (cellules d'en-tête surlignées) : cellules colorées (#DBE5F1) en avril,
  mai et juin. » La phrase est ce qui transporte la couleur jusqu'au LLM sans qu'il ait à
  interpréter de géométrie.
- `overlays/page_NNN.png` : la page avec ce que la détection a compris (bleu : grille et
  colonnes ; rouge : croix ; orange : couleur ; violet : bande ; vert : image routée en vision ;
  gris : image ignorée ; magenta : suspect ou scan). C'est l'outil de vérification humaine.

---

## 4. Module 2 — vision ciblée

Fichier : `m2_vision/vision.py`.

1. **Sélection** : entrées de routage `vision_zone` et `ocr_page` (filtrables par priorité),
   plus en option `--verifier-grilles`, qui envoie aussi les grilles vectorielles. C'est un mode
   d'**audit** pour comparer lecture géométrique et lecture vision sur un nouveau format de BE.
2. **Recadrage** : zone + marge de 6 pt, rendue à 200 dpi en PNG.
3. **Moteur** :
   - `ocr` (Mistral OCR) pour les `document_raster` et pages scannées : texte et tableaux ;
   - `vision` (modèle multimodal, `response_format` JSON) pour les grilles et zones suspectes,
     avec un prompt qui dit explicitement que l'information peut n'être portée que par la couleur
     et qui impose un schéma (colonnes, lignes, marques croix/couleur/bande, incertitudes).
4. **Cache** : empreinte SHA-256 de (image + prompt + modèle). Relancer ne refait aucun appel ;
   changer de modèle ou de prompt invalide proprement.
5. **Robustesse** : transport HTTP de la bibliothèque standard (pas de dépendance au SDK
   `mistralai`, dont les versions 1.x et 2.x sont incompatibles), 3 tentatives avec attente
   exponentielle sur 429/5xx, une erreur n'arrête pas les autres zones (`statut: erreur`).
6. **Dry-run** : produit les recadrages et les prompts sans appel, pour relire ce qui partirait.

Modèles par défaut, surchargeables par variables d'environnement ou options :
`MISTRAL_VISION_MODEL` (défaut `mistral-medium-latest`) et `MISTRAL_OCR_MODEL`
(défaut `mistral-ocr-latest`). **Les appels réels n'ont pas pu être exécutés dans l'environnement
de développement** (pas de clé ni d'accès réseau) : le code est testé par simulation
(`tests/test_m2_simulation.py`). Le nom du modèle multimodal et le format du message image sont à
valider au premier appel réel.

---

## 5. Module 3 — assemblage et contrôles

Fichiers : `m3_assemblage/assemblage.py`, `m3_assemblage/controles.py`.

### 5.1 Texte enrichi

- Texte de base : l'OCR déjà calculé par le pipeline (`--ocr`, liste de pages ou sortie Mistral
  `{"pages": [{"markdown": …}]}`), sinon la couche texte native. **Il n'est jamais modifié.**
- Format de page `=== page N ===`, identique à la passe 0 du pipeline d'ingestion.
- En fin de page, un bloc `<<<ENRICHISSEMENT page=N>>> … <<<FIN_ENRICHISSEMENT>>>` contient :
  - les grilles lues (tableau + lecture en phrases), étiquetées « lecture géométrique du PDF
    (sans OCR), confiance … » ;
  - les transcriptions du module 2, étiquetées `source=vision, à vérifier` ;
  - pour toute zone routée mais non transcrite, un marqueur explicite : « contenu EN IMAGE NON
    TRANSCRIT… Ne rien déduire de cette zone ; signaler l'information manquante. » Le LLM
    d'extraction sait alors qu'il doit produire un `champ_a_confirmer` plutôt qu'inventer.

### 5.2 Contrôles

| Contrôle | Logique | Niveau |
|---|---|---|
| `concordance_mois` | Mois marqués dans chaque grille mensuelle comparés aux mois cités en toutes lettres dans la prose : même page, puis jusqu'à 2 pages avant, en s'arrêtant sur une page qui a sa propre grille de mois (autre fiche). En-têtes et pieds de page courants exclus (lignes présentes sur ≥ 30 % des pages : sans ce filtre, « Avril 2025 » de l'en-tête courant passait pour un mois cité). | `couleur_seule` → alerte ; `divergent` → alerte ; `partiel` → attention ; `concordant` → info |
| `libelles_annees` | Sur un axe d'années, l'année de référence N = année − k doit être la même pour toutes les colonnes. | attention |
| `plage_agregee` | Marque posée sur une colonne pluriannuelle (« N+6 à N+10 ») : l'année exacte n'est pas dans la grille. | info |
| `zone_non_lue` | Zone routée en vision sans transcription disponible. | alerte si priorité haute |

---

## 6. Résultats sur le plan Rivière Environnement (Bruges / Sainte-Eulalie)

49 pages (pages imprimées 162 à 210), traitement du module 1 en 5,8 s (12 s pour les trois modules).

| Page PDF (imprimée) | Grille | Lecture |
|---|---|---|
| 24 (185) | Tableau 39, 2025-2040 | 8 lignes : croix N, bandes de récurrence, ES1/ES2 X de N à N+5 puis « 1 suivi tous les 5 ans » |
| 25 (186) | fin du tableau 39 + tableau 40 | FG2 : X en N+5 puis bande ; RG1/RG3/ES1/ES2 de N+16 à N+30 |
| 26 (187) | tableau 40 (fin) | FG1, FG2 |
| 30 (191) | RG1, mois + phasage | **mars, avril, juillet, août** (couleur) ; X en N, « Fauche et griffage » de N+1 à N+30 |
| 33 (194) | RG2, mois + phasage | **septembre, octobre** (couleur, libellé « Mar/s » reconstruit) |
| 36 (197) | ES1, mois + phasage | **juin, juillet** (couleur) |
| 37 (198) | ES2, mois + phasage | **avril, mai, juin** (couleur) |
| 40 (201) | FG1 phasage | X sur les 11 colonnes |
| 41 (202) | FG2 phasage | bilans intermédiaires N+5, N+6-10, … ; bilan final N+26-30 |

Ce que la géométrie a apporté :

- **La fenêtre d'ES2 (avril-mai-juin) n'existe que dans la couleur.** Le contrôle la remonte en
  alerte `couleur_seule`. Avec la seule chaîne OCR → LLM, l'échéance ES2 serait sortie sans
  fenêtre d'intervention.
- RG1, RG2 et ES1 : grille et prose concordent. RG2 n'est concordant qu'en remontant 2 pages, la
  fiche commençant page 31 (« septembre/ octobre »).
- Libellés d'années incohérents sur ES1, ES2, FG1 et FG2 (« N+16 à N+20 » = « 2041à 2046 »,
  « N+21 à N+25 » = « 2047 à 2053 »…) : signalés, avec la consigne de se fier aux N+k.
- 3 images routées en vision : composition des bosquets (p. 193), schéma juridique ERC (p. 207),
  liste d'espèces de l'annexe 1 (p. 210). 21 photos et cartes ignorées.

---

## 7. Évaluation

### 7.1 Outils

- `evaluation/generer_corpus.py` génère 13 PDF synthétiques, chacun isolant une difficulté.
- `evaluation/evaluer.py` compare le profil à une vérité terrain JSON (format décrit en tête du
  fichier) et calcule précision et rappel **par cellule** et par type de marque, plus les grilles
  en trop, la classification des images, la détection des scans et le routage.
- `lancer_tests.sh` enchaîne corpus, vérité terrain des plans réels et test du module 2 simulé.

| Cas | Difficulté isolée |
|---|---|
| c01 | Fiche Word : mois surlignés dans l'en-tête, consigne pleine largeur dessous |
| c02 | Gantt mensuel, bordures en traits tracés, couleurs dans le corps |
| c03 | Programmation pluriannuelle : croix, bande colorée, ligne vide, bande non colorée |
| c04 | Mois abrégés, ligne entièrement grisée avec couleur minoritaire |
| c05 | Mois en initiales J F M A M J J A S O N D |
| c06 | Aucune bordure : libellés et barres colorées seuls |
| c07 | Grille collée en image → doit partir en vision |
| c08 | Page numérisée → doit partir en OCR |
| c09 | Distracteurs : prose dense en mois et N+k, tableau d'années en colonne |
| c10 | Tableau budgétaire avec années en colonnes : grille reconnue, aucune fausse marque |
| c11 | En-tête de mois coloré uniformément (style) + croix : la couleur ne doit pas compter |
| c12 | Deux couleurs dans une ligne, cellule fusionnée colorée sans texte |
| c13 | Colonne « État zéro » avant N |

### 7.2 Résultats

| Jeu | Grilles | En trop | Croix P/R | Couleur P/R | Bandes P/R | Images | Scans | Routage |
|---|---|---|---|---|---|---|---|---|
| Corpus synthétique régénéré (13 cas, reportlab) | 10/10 | 0 | 100 %/100 % | 100 %/100 % | 100 %/100 % | 1/1 | 1/1 | 2/2 |
| Corpus figé (13 cas, autre producteur PDF) | 10/10 | 0 | 100 %/100 % | 100 %/100 % | 100 %/100 % | 1/1 | 1/1 | 2/2 |
| Plan Rivière (réel) | 14/14 | 0 | 100 %/100 % (47 cellules) | 100 %/100 % (11) | 100 %/100 % (243) | 7/7 | — | 2/2 |

Le contrôle de l'évaluateur lui-même a été fait en injectant une vérité terrain fausse : les deux
écarts introduits sont bien signalés.

### 7.3 Ce que ces chiffres valent

**Ils ne prouvent pas la généralisation.** Le corpus synthétique et la vérité terrain du plan
Rivière ont été écrits par le même auteur que le détecteur ; la vérité terrain réelle a été
établie à l'œil sur les rendus des pages, mais après avoir vu une partie des sorties. La mesure
qui compte est celle obtenue sur des plans **d'autres bureaux d'études, annotés par quelqu'un
d'autre**. Ordre conseillé :

1. Le Barp (ECO-COMPENSATION / SIMETHIS / FAGE, 102 p.) : sa vérité terrain JSON existe déjà
   (74 objets action-récurrence) ; il faut la convertir au format `attendus` à partir des tableaux
   6 à 9 et des frises. C'est le test le plus exigeant, car environ la moitié des années exactes
   y sont portées par des grilles colorées, dont les frises SE1 et MG2 sans aucun texte.
2. Testemaure (SIMETHIS) : frises à croix en texte, cas « facile » qui doit rester à 100 %.
3. Chaque nouveau BE : annoter 2 ou 3 fiches suffit (le format accepte une vérité partielle si
   `exhaustif` vaut false).

---

## 8. Limites connues et pistes

| Limite | Effet | Piste |
|---|---|---|
| En-têtes temporels **pivotés** (mois écrits verticalement) | les mots pivotés sont lus, mais la recherche de graines ne parcourt que les lignes horizontales | appliquer la recherche de graines aux colonnes de mots pivotés |
| Tableau qui continue sur une page **sans répéter l'en-tête** | lignes non lues sur la page suivante | reprendre les colonnes de la dernière grille de la page précédente si les traits verticaux sont aux mêmes x |
| Axes « Année 1 / An 1 », trimestres « T1…T4 », mois groupés « Mars-Avril » | non reconnus comme axe | étendre `temporel.jeton_temporel` ; chaque ajout doit être couvert par un cas du corpus |
| Barres de Gantt non alignées sur les cellules | la couleur est testée au centre de la colonne : une barre couvrant un quart de cellule peut être manquée | mesurer le taux de recouvrement de l'aplat sur la cellule plutôt que le point central |
| Hachures ou motifs au lieu d'aplats | non vus comme marques | compter les petits traits obliques dans la cellule |
| Glyphes de marque inhabituels (« ◆ », « 2 passages ») | typés `texte`, pas `croix` | ils restent visibles dans la sérialisation ; enrichir `RE_CROIX` au besoin |
| Seuils de classification des images | calibrés sur un seul plan réel | à recaler sur le corpus de plans réels ; en cas de doute, `document_raster` est le choix prudent |
| Module 2 non exécuté en réel | modèle et format d'image à valider | lancer `--verifier-grilles` sur 2-3 plans pour mesurer l'accord vision / géométrie |

### Dépendances et licences

| Rôle | Bibliothèque | Licence |
|---|---|---|
| Géométrie : mots, rectangles, lignes, couleurs | pdfplumber (sur pdfminer.six) | MIT |
| Rendu des pages et zones en image | pypdfium2 (moteur PDFium) | Apache-2.0 / BSD-3 |
| Mesures sur les images | numpy, Pillow | BSD / HPND |
| Génération du corpus de test (développement) | reportlab | BSD |

Aucune dépendance AGPL : la v1.0 utilisait PyMuPDF (AGPL-3.0), retiré en v1.1. Le portage n'a
touché que `geometrie.py` (classe `DocumentPDF`, extraction des mots et des tracés), les rendus
(`images.py`, `overlay.py`, recadrage du module 2) et la lecture du texte natif du module 3.

**Équivalence vérifiée** : sur le plan Rivière, les 14 grilles produisent exactement les mêmes
colonnes et les mêmes marques qu'avec PyMuPDF, la même classification des 24 images, le même
routage et les mêmes contrôles. Seul changement : le module 1 passe de 1,8 s à 5,8 s pour 49
pages (pdfminer est en Python pur).

**Deux producteurs PDF dans les tests** : le corpus figé (`evaluation/corpus_fige/`, PDF écrits
par un autre générateur que reportlab) a révélé le piège `re h f` → courbe, invisible sur le
corpus régénéré et sur le plan réel. Tout nouveau générateur rencontré chez un BE (Word,
LibreOffice, Excel exporté, InDesign, QGIS…) mérite un PDF de plus dans ce jeu figé.

---

## 9. Intégration dans le pipeline d'ingestion KerERC

1. **Passe 0** : après l'OCR Mistral et pdftotext, lancer le module 1 sur le PDF (même cache par
   SHA-256). Coût nul, environ 6 s pour 50 pages.
2. **Module 2** sur les seules zones routées, puis **module 3** avec `--ocr` pointant sur le cache
   OCR existant : le `document_enrichi.md` remplace le texte fusionné en entrée des passes 1 et 3.
   La carte du document et le découpage par fiches ne changent pas, les blocs d'enrichissement
   étant en fin de page.
3. **Gain direct possible sur l'extraction** : les grilles mensuelles lues donnent la
   `fenetre_intervention` (MM-DD) de manière déterministe. Plutôt que de la faire relire par le
   LLM, on peut l'injecter comme fait vérifié dans le contrat d'échéance, avec la page source et
   `origine = geometrie`.
4. **Contrôles → questions BE** : `couleur_seule`, `divergent`, `libelles_annees` et
   `zone_non_lue` alimentent directement la liste des points à confirmer de l'étape 2.5.

---

## 10. Commandes

```bash
pip install -r pdfvisuel/requirements.txt   # Python ≥ 3.10

# Tout le pipeline (module 2 en dry-run par défaut)
python -m pdfvisuel.pipeline plan.pdf -o sortie/ [--vision non|dry-run|reel] [--ocr cache_ocr.json]

# Modules séparés
python -m pdfvisuel.m1_detection.profil plan.pdf -o sortie/ [--pages 20-45] [--overlays tout]
python -m pdfvisuel.m2_vision.vision sortie/profil.json [--dry-run] [--verifier-grilles]
python -m pdfvisuel.m3_assemblage.assemblage sortie/profil.json [--ocr cache_ocr.json]

# Évaluation
python -m pdfvisuel.evaluation.generer_corpus corpus/
python -m pdfvisuel.evaluation.evaluer corpus/attendus/*.json mes_attendus/*.json \
       --pdf-dir corpus --pdf-dir mes_plans -o rapport.md
./pdfvisuel/lancer_tests.sh mes_plans/
```

Arborescence :

```
pdfvisuel/
  m1_detection/   geometrie.py temporel.py grilles.py images.py serialisation.py overlay.py profil.py
  m2_vision/      vision.py
  m3_assemblage/  assemblage.py controles.py
  evaluation/     generer_corpus.py mini_pdf.py evaluer.py attendus_reels/ corpus_fige/
  tests/          test_m2_simulation.py
  pipeline.py  lancer_tests.sh  DOC_PIPELINE.md  README.md  requirements.txt
```