"""Tous les prompts, au même endroit (français : les documents le sont).

Principe commun : le modèle **interprète** (règles, fréquences, rattachements),
il ne **calcule** jamais les occurrences — c'est le rôle de s6_expand.
"""

SYSTEM_BASE = (
    "Tu es un écologue expert de la séquence ERC et des plans de gestion de sites de compensation "
    "écologique en France. Tu lis des documents techniques et tu en extrais des informations "
    "structurées, fidèles au texte. Tu ne complètes jamais une information absente : tu la laisses "
    "vide et tu le signales. Chaque affirmation importante est appuyée par une citation copiée mot "
    "pour mot avec son numéro de page [PAGE n]."
)

# --------------------------------------------------------------------------- s2
PAGE_ROLES = """Voici des extraits de pages d'un document (début de chaque page).
Pour chaque page, donne :
- `roles` (un ou plusieurs) parmi : cadre (durée, période couverte, définition de l'année N, opérateur),
  unites_gestion, catalogue_actions (liste ou tableau récapitulatif des actions), fiche_action,
  programmation (calendrier pluriannuel, grille d'années), suivi_evaluation, couts, hors_plan, autre ;
- `fiche_code` : code de l'action si la page est une fiche action OU la suite d'une fiche
  (une fiche peut s'étendre sur plusieurs pages sans répéter son titre — utilise la page précédente) ;
- `in_plan_de_gestion` : false si la page relève d'une autre partie du document
  (état initial du site impacté, justification du projet, annexes sans rapport…).

Indices détectés automatiquement (codes repérés, en-têtes de grille) :
{hints}

Pages :
{pages}
"""

# --------------------------------------------------------------------------- s3
GRID_VISION = """Cette image est une page d'un plan de gestion. Relève **toutes** les grilles temporelles
qu'elle contient :
- grilles pluriannuelles (colonnes N, N+1… ou années 2025, 2026… ou plages « N+6 à N+10 ») ;
- grilles mensuelles (colonnes Janvier … Décembre).

Pour chaque ligne de grille : code de l'action (ex. RG1) s'il est visible dans la ligne ou dans une
cellule fusionnée au-dessus, libellé de l'action, libellé de la sous-action, puis les cellules actives :
- `mark` : cellule contenant une croix / un symbole ;
- `band` : cellule fusionnée couvrant plusieurs colonnes, avec son texte (« 1 à 2 fauches annuelles… ») ;
- `fill` : cellule seulement colorée (pas de texte) — c'est fréquent pour les mois d'intervention.
Pour `columns`, recopie les libellés exacts des colonnes couvertes (ex. ["N+1", "N+2"] ou ["Mars", "Avril"]).
Une cellule vide et non colorée n'est pas listée. Ne devine pas une ligne que tu ne vois pas.
"""

# --------------------------------------------------------------------------- s4
FRAME = """Document : plan de gestion (ou dossier qui en contient un). Texte complet ci-dessous,
découpé par marqueurs [PAGE n] (numéro de page PDF ; le numéro imprimé est entre parenthèses).

Les grilles temporelles du document ont été relues séparément (géométrie + couleurs des cellules) ;
leur transcription est fournie après le texte : elle fait foi pour les cases cochées/colorées.

Extrais le CADRE du plan de gestion :
1. `duration_years` : durée de gestion couverte (ex. « 30 ans de gestion »). Attention : ne confonds pas
   avec la durée d'un chantier, d'un suivi de chantier, d'une phase de suivi annuel (« les 5 premières
   années ») ou d'une périodicité (« tous les 5 ans »). Cite la phrase qui fixe la durée.
2. `n_definition` : ce que représente l'année N (ex. « année d'engagement des travaux compensatoires »).
3. `n_year`, `start_year`, `end_year` si le document les donne (ex. tableau « N = 2025 », « 2025-2055 »).
4. `ugs` : unités de gestion / secteurs / zones de gestion, avec code tel qu'écrit et surface.
5. `actions` : TOUTES les actions codées du plan (restauration, gestion, suivi, animation, évaluation…),
   avec leurs UG (`ug_codes`) ou `site_wide=true` si l'action porte sur tout le site sans UG
   (animation, bilans…). Prends les rattachements dans les fiches (« Localisation ») et les tableaux.
6. `notes` : incohérences internes repérées (codes en double, surfaces divergentes, coquilles de code…).

=== TEXTE ===
{document}

=== GRILLES RELUES ===
{grids}
"""

RULES = """=== TEXTE DU PLAN DE GESTION ===
{document}

=== GRILLES RELUES (géométrie + couleurs ; font foi pour les cases cochées / colorées) ===
{grids}

=== CADRE DÉJÀ EXTRAIT ===
{frame}

=== TÂCHE ===
Extrais les RÈGLES TEMPORELLES de l'action **{code}** ({title}) et d'elle seule.

Une règle = une sous-action × un motif temporel × un ensemble d'UG. Découpe en plusieurs règles quand
le motif change (ex. « annuel de N à N+5 puis tous les 5 ans » = 2 segments d'une même règle ;
« travaux initiaux en N » et « entretien ensuite » = 2 règles).

Années : relatives à N (0 = N, 1 = N+1…). Si le document donne des années calendaires, convertis avec
N = {n_year}. Si une sous-action est ancrée sur un autre événement (« l'année suivant la plantation »,
« après réception des travaux »), renseigne `anchor` et exprime les années relativement à cet événement.

`kind` :
- ponctuelle : une ou quelques années explicites (travaux initiaux, bilan final…) ;
- recurrente : série périodique (annuelle, tous les k ans, n fois par an) ;
- continue : activité permanente sans date (animation, veille, coordination) ;
- conditionnelle : déclenchée par un constat (« si », « en cas de », « au besoin », « éventuel ») → `trigger` ;
- non_precisee : récurrence annoncée sans fréquence exploitable (« entretien » sans rythme).

`segments` : utilise `years` pour des années explicites ; sinon `start`/`end`/`step_min`/`step_max`.
« tous les 2 à 3 ans » → step_min=2, step_max=3. « une dizaine de visites sur la durée du plan » →
`count_over_period`=10. `end` = null signifie « jusqu'à la fin du plan ».
Si la grille montre une bande (cellule fusionnée) sans préciser son début, le début de la bande donne `start`.

`passes` : passages dans l'année. « 1 à 2 fauches par an, au printemps et en été » → 2 passages,
le second `optional=true`. Les mois viennent du texte ou des cellules colorées des grilles mensuelles.

Ne crée pas de fréquence absente du texte : mets kind=non_precisee et explique dans `ambiguities`.
Signale dans `ambiguities` toute contradiction entre fiche, tableau de programmation et texte, en
indiquant la valeur que tu as retenue et pourquoi.
`confidence` : ta confiance (0-1) que la règle est complète et exacte.
Joins 1 à 3 citations exactes par règle (page + extrait ≤ 200 caractères).
"""

# --------------------------------------------------------------------------- s5
CRITIC = """=== TEXTE DU PLAN DE GESTION ===
{document}

=== GRILLES RELUES ===
{grids}

=== CADRE ===
{frame}

=== RÈGLES EXTRAITES POUR L'ACTION {code} ===
{rules}

=== PROBLÈMES DÉTECTÉS AUTOMATIQUEMENT ===
{issues}

=== TÂCHE ===
Relis le document et corrige les règles de l'action {code}. Pour chaque problème : tranche à partir
du texte et des grilles, ou, si le document est réellement contradictoire, garde la valeur la plus
étayée et décris la contradiction dans `ambiguities`. Ne supprime pas une règle étayée par une citation.
Ajoute une règle si une sous-action documentée manque. Liste chaque correction dans `changes`
(« règle X : champ Y passé de A à B car … »). Si tu vois des actions codées absentes du cadre,
liste leurs codes dans `missing_actions`.
"""
