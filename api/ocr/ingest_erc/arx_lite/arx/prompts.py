"""Prompts (français). Principe : le modèle lit l'acte ENTIER à chaque appel (préfixe identique,
mis en cache) et se concentre sur une tâche ; chaque information est citée mot pour mot."""

SYSTEM = (
    "Tu es juriste en droit de l'environnement et écologue, spécialiste des arrêtés préfectoraux "
    "(dérogations espèces protégées, autorisations environnementales, séquence ERC). Tu extrais des "
    "informations structurées fidèles au texte. Tu n'inventes rien : une information absente reste vide. "
    "Chaque élément important est appuyé par une citation copiée mot pour mot avec son numéro de page "
    "[PAGE n]. Les dates sont au format AAAA-MM-JJ."
)

DOC = """=== ACTE (texte intégral, marqueurs [PAGE n]) ===
{document}

=== STRUCTURE DÉTECTÉE AUTOMATIQUEMENT ===
{structure}
"""

FICHE = DOC + """
=== TÂCHE : FICHE D'IDENTITÉ DE L'ACTE ===
Remplis l'inventaire des métadonnées :
- référence, titre, type d'acte, procédure / régime juridique, autorité, date et lieu de signature ;
- projet (nom, nature avec chiffres clés, communes, département, région) ;
- parties : bénéficiaire (nom, forme juridique, adresse, identifiant), autorité, signataire et sa qualité
  (délégation), service instructeur, opérateurs, écologue, destinataires en copie, avec les contacts cités ;
- bases juridiques : chaque article / texte cité dans les VU, une entrée par texte ;
- visas : un par VU, typé, avec date(s) (début et fin pour une consultation), auteur, référence.
  Les VU décrivent l'historique de l'instruction : demande, compléments, avis, réponses, consultation ;
- actes liés : arrêtés antérieurs visés et relation (modifie, complète, abroge, proroge, transfère, vise) ;
- recours : délai, juridiction, modalités ; publication ; copies.
"""

SYNTHESE = DOC + """
=== TÂCHE : SYNTHÈSE EXHAUSTIVE PAR RUBRIQUES ===
Objectif : permettre de survoler l'acte sans le relire. Sois exhaustif sur les faits, concis sur la forme.
- resume_general : 3 à 5 phrases (quoi, où, pour qui, grandes familles de mesures, durées).
- justification : résumé des CONSIDÉRANT, motifs (intérêt public majeur, absence d'alternative, état de
  conservation…), chiffres clés (logements, surfaces…).
- especes : TOUTES les espèces visées, avec nom scientifique, nom vernaculaire, groupe et la ou les natures
  de dérogation qui les concernent (une espèce peut relever de plusieurs natures).
- impacts : chaque impact autorisé, avec ses quantités (valeur, unité, objet) et espèces concernées.
- perimetre : comment le périmètre est défini (figure, plan, parcelles).
- durees : chaque durée ou date limite structurante (phase chantier, compensation, gestion, sécurisation).
- sites : site du projet, secteurs évités, sites de compensation in situ / ex situ (commune, surface,
  opérateur, figures).
- gouvernance : comité de suivi, composition, fréquence.
- points_cles : 5 à 12 points à retenir, concrets et chiffrés (plafonds d'impact, durées, échéances majeures,
  engagements quantifiés).
"""

RULES_ARTICLE = """1. resume : 2 à 4 phrases fidèles, chiffres inclus. categories : une ou plusieurs.
2. mesures : chaque mesure E/R/C/A/S décrite par l'article (pas celles seulement mentionnées par renvoi),
   avec quantités, espèces cibles, prescriptions techniques (une par entrée), codes du dossier cités, figures.
3. obligations : décompose l'article en obligations élémentaires — une action, un livrable, une
   interdiction, une période imposée, une déclaration conditionnelle. Une liste ou un tableau de documents
   à transmettre donne une obligation par ligne. Une mesure de terrain donne au moins une obligation
   « Mettre en œuvre … » avec sa période ou sa durée.
   Pour chaque obligation, structure l'échéance :
   - « au plus tard 15 jours avant le démarrage des travaux » → kind=delai_avant, anchor=demarrage_travaux, offset 15 jour ;
   - « dans un délai de 6 mois à compter de la notification » → delai_apres, notification, 6 mois ;
   - « à l'issue de ces travaux (délai de 15 jours) » → delai_apres, anchor=fin_travaux ou evenement_ponctuel ;
   - « au plus tard le 31 mars de l'année N+1 » → date_annuelle, day_month=03-31, year_offset=1 ;
   - « le 31/03 de l'année N+4, N+6, N+11 » → annees_relatives, years_relative=[4,6,11], day_month=03-31,
     anchor = événement qui définit N dans l'acte ;
   - « entre le 1er septembre et mi-février » → periode_autorisee, window_start=09-01, window_end=02-15 ;
   - « aucune fauche de mai à juillet » → periode_interdite ; « pendant toute la durée du chantier » → permanente ;
   - « sans délai », « dès qu'il en a connaissance » → sans_delai ; aucune indication → non_precisee.
   Pour un événement récurrent (« chaque passage de l'écologue ») : anchor=evenement_recurrent + anchor_label.
   `preuve_attendue` : type(s) de document qui prouveront que l'obligation est remplie.
   `condition` : renseignée si l'obligation ne s'applique que dans un cas (« si », « en cas de », « le cas échéant »).
   `ambiguities` : formulations floues ou contradictoires avec un autre article (cite l'article).
4. references_internes : articles de l'acte cités ; figures : numéros des figures citées.
Chaque mesure et chaque obligation porte 1 à 2 citations exactes.


CONCISION (la longueur de la réponse fait le temps de traitement) : résumés de 2 à 3 phrases ;
`description` vide sauf nécessité ; une seule citation par obligation (≤ 150 caractères) ; ne répète pas
dans `mesures` les prescriptions déjà portées par les obligations ; champs vides omis.
"""

ARTICLE = DOC + """
=== TÂCHE : ANALYSE DE L'ARTICLE {numero} ({titre}) ===
Texte de l'article :
{texte}

Analyse CET article seulement (le reste de l'acte sert de contexte pour comprendre les renvois) :
""" + RULES_ARTICLE

ARTICLES_BATCH = DOC + """
=== TÂCHE : ANALYSE DES ARTICLES {numeros} ===
Pour CHACUN de ces articles (et seulement eux), renvoie une entrée `articles` avec son `numero`
exactement comme dans la structure détectée. Le reste de l'acte sert de contexte pour les renvois.
Une obligation énoncée par un article n'est rattachée qu'à cet article.
""" + RULES_ARTICLE

CONTROLES = DOC + """
=== OBLIGATIONS EXTRAITES (résumé) ===
{obligations}

=== TÂCHE : CONTRÔLE DE COHÉRENCE INTERNE ===
Relève les CONTRADICTIONS et INCOHÉRENCES internes de l'acte : mêmes documents ou mêmes actions avec des
échéances, fréquences, quantités ou durées différentes selon les articles ; renvois vers un article qui ne
dit pas ce qui est annoncé ; numérotation ou chiffres incohérents ; formulations impossibles à appliquer
(ex. délai qui précède la date de l'acte). Pour chaque point : sujet, description, articles concernés,
valeurs en conflit, citations exactes de chaque côté. Ne signale pas de simples redites cohérentes.
"""

FIGURE = """Cette image contient la figure {numero} d'un arrêté préfectoral (« {legende} »).
Décris son contenu utile (type de plan, éléments représentés) et relève TOUTES les valeurs et codes lisibles
dans la légende ou sur la carte (surfaces, longueurs, codes de mesures comme E02 ou R05, codes d'unités
de gestion, nombres de symboles par catégorie si comptables)."""

SINGLE = DOC + """
=== TÂCHE : ANALYSE COMPLÈTE EN UNE PASSE ===
Renvoie en un seul objet :
1. `fiche` — FICHE D'IDENTITÉ :""" + FICHE.split("Remplis l'inventaire des métadonnées :")[1] + """
2. `synthese` — SYNTHÈSE :""" + SYNTHESE.split("=== TÂCHE : SYNTHÈSE EXHAUSTIVE PAR RUBRIQUES ===")[1] + """
3. `articles` — une entrée par article ou sous-article ayant un texte propre (numéro exact de la structure) :
""" + RULES_ARTICLE
