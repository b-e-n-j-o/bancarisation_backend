# arx — analyse structurée des arrêtés préfectoraux

Prototype dédié aux **arrêtés** : dérogations espèces protégées, autorisations environnementales,
arrêtés complémentaires ou modificatifs. Il produit un JSON en quatre niveaux de lecture et un rapport lisible.
La lecture des figures est prévue mais désactivée pour l'instant.

## Ce que produit l'analyse (`arrete.json`, défini dans `arx/schemas.py`)

| Niveau | Contenu | Usage en interface |
|---|---|---|
| `fiche` | Référence, type d'acte, procédure, autorité, signature (date, lieu, délégation), projet, parties (bénéficiaire, service instructeur, copies, contacts), bases juridiques, **VU typés et datés** (historique de l'instruction), actes liés, recours, publication | En-tête de la page « Arrêté », frise de l'instruction |
| `synthese` | Résumé général, justification (motifs, chiffres clés), **espèces** (nom scientifique, groupe, natures de dérogation), **impacts autorisés chiffrés** (plafonds), périmètre, **durées**, **sites** (projet, évités, compensation in situ / ex situ), gouvernance, points clés, et des compteurs calculés (espèces par groupe et par nature, mesures par type) | Survol de l'acte sans le relire |
| `articles` | Chaque article et sous-article (arborescence 16 → 16.1), pages, texte, résumé, catégories, **mesures** E/R/C/A/S (quantités, prescriptions, codes du dossier, figures), obligations, renvois internes | Rubriques dépliables, article par article |
| `obligations` | Liste à plat : intitulé, catégorie, débiteur, destinataire, **échéance structurée**, **preuve attendue** (type de document), condition de déclenchement, quantités, citations, **confiance** et décision, `statut` (non évalué pour l'instant) | Liste de contrôle de conformité |
| `echeancier` | Dates calculées à partir des ancres connues (signature, notification, démarrage des travaux…), séries (N+4, N+6…), fenêtres autorisées ou interdites, hypothèses affichées | Calendrier réglementaire |
| `figures` | Légendes et pages (contenu et valeurs si lecture vision activée) | Liens vers les plans |
| `controles` | Citations introuvables, **contradictions internes** (par règle et par LLM), échéances possiblement manquées, problèmes de découpage | Points d'attention |

Échéance structurée (`Echeance.kind`) : `date_fixe`, `delai_apres`, `delai_avant`, `date_annuelle`,
`annees_relatives`, `periode_autorisee`, `periode_interdite`, `permanente`, `sans_delai`, `non_precisee`,
avec une ancre (`notification`, `demarrage_travaux`, `fin_travaux_compensatoires`, `evenement_recurrent`…),
un délai, un jour-mois, des années relatives et une fenêtre. Le texte d'origine est toujours conservé.

Le champ `statut` et le champ `preuves` de chaque obligation sont prévus pour l'étape suivante : chaque
document ingéré (CR, plan de gestion, GéoMCE…) cochera ou contredira les obligations.

## Étapes : un fichier par étape

| Fichier | Étape | Rôle |
|---|---|---|
| `arx/config.py` | — | Réglages : modèles, k, seuils, options |
| `arx/schemas.py` | — | **Format de sortie** et modèles demandés au LLM |
| `arx/llm.py` | — | Client Mistral (chat JSON, vision, OCR, cache, journal), identique à celui de pgcal |
| `arx/prompts.py` | — | Tous les prompts |
| `arx/s1_ingest.py` | 1 | PDF → texte par page (natif « layout », sinon OCR Mistral) ; retrait des numéros de page et des en-têtes et pieds répétés |
| `arx/s2_segment.py` | 2 | **Découpage déterministe** : en-tête, référence, VU, CONSIDÉRANT, ARTICLE n et sous-articles (3 niveaux), TITRE et CHAPITRE, signature, annexes, légendes de figures ; contrôles de numérotation |
| `arx/s3_extract.py` | 3 | LLM, **1 à quelques appels en parallèle** : fiche, synthèse et lots d'articles (ou un seul appel en mode `single`) ; compteurs |
| `arx/s4_verify.py` | 4 | Vérification des citations, fusion des obligations redites, **contradictions** (règle + LLM), couverture, confiance et décision |
| `arx/s5_schedule.py` | 5 | Échéancier calculé à partir des ancres |
| `arx/s6_export.py` | 6 | `arrete.json` et `rapport.md` |
| `arx/pipeline.py`, `arx/__main__.py` | — | Orchestration (artefacts par étape) et ligne de commande |
| `eval/evaluate.py` | — | Comparaison avec une référence : fiche, espèces, articles, obligations, échéances, contradictions |
| `examples/bruges/` | — | Arrêté DBEC 149/2025 : `expected_llm.json` (réponses attendues), `arrete_reference.json`, `rapport.md` |
| `tests/` | — | Découpage, échéancier, contradictions, chaîne complète injectée, plomberie LLM simulée |

## Utilisation

```bash
pip install -r requirements.txt            # + poppler-utils conseillé
export MISTRAL_API_KEY=...

python -m arx segment arrete.pdf                                     # découpage seul, sans API
python -m arx run arrete.pdf --anchor demarrage_travaux=2025-12-05   # analyse complète
python -m arx run arrete.pdf --mode single                           # un seul appel (actes courts)
python -m arx run arrete.pdf -k 2 --controls --figures               # version approfondie, plus lente
python -m arx run arrete.pdf --offline --inject examples/bruges/expected_llm.json --anchor demarrage_travaux=2025-12-05
python eval/evaluate.py runs/<run>/arrete.json examples/bruges/arrete_reference.json
ARX_TEST_PDF=/chemin/arrete.pdf pytest -q tests/
```

## Choix de conception

- **Texte, pas image, pour l'instant.** Le texte natif « layout » suffit pour les arrêtés numériques,
  y compris les tableaux d'échéances, qui restent alignés. L'OCR Mistral ne sert qu'aux pages sans texte
  (`--force-ocr` pour tout passer en OCR). La lecture des figures existe (`--figures`) mais reste désactivée :
  sur Bruges, elle apporterait des surfaces évitées ou impactées, des codes du DDEP et la correspondance avec les UG.
- **Peu d'appels, lancés en parallèle.** Ce qui coûte du temps, c'est la longueur de la *sortie*
  (environ 30 000 tokens pour tout l'arrêté de Bruges), pas le nombre d'appels. Le mode par défaut
  (`parallel`) lance en même temps un appel « fiche », un appel « synthèse » et des lots d'articles
  d'environ 20 000 caractères. Pour Bruges, cela fait **5 appels simultanés** (lots 1–11.3, 11.4–13.2, 14–21) :
  la durée totale est celle de l'appel le plus long. Un acte court (moins de 25 000 caractères) passe en
  **un seul appel** (`--mode single`). Le texte intégral reste dans chaque appel pour comprendre les renvois
  (préfixe identique, mis en cache). Les consignes de concision (une citation par obligation, résumés de
  2 à 3 phrases) réduisent la sortie.
- **Options coûteuses, désactivées par défaut** : `-k 2` (deux échantillons pour mesurer l'accord),
  `--controls` (un appel de plus pour chercher les contradictions), `--figures` (vision).
  Les contrôles déterministes (citations, fusion, contradictions par règle, échéancier) tournent toujours :
  ils sont gratuits.
- **Obligations atomiques.** Une ligne de tableau ou une prescription donne une obligation. Une obligation redite
  dans un autre article est **fusionnée** (`articles_lies`). Si elle est redite avec une échéance
  **différente**, c'est une **contradiction** signalée.
- **Confiance par obligation** = 0,35 citations retrouvées + 0,30 accord entre échantillons
  (1 si k = 1) + 0,20 précision de l'échéance + 0,15 auto-évaluation du modèle.
  Décision : `retenue` (≥ 0,80 sans alerte), `a_valider` (≥ 0,55), `a_revoir` sinon.
- **Ancres** : la signature est lue dans l'acte. Sans date de notification, on prend la signature, et
  l'hypothèse est affichée. Les autres ancres (démarrage des travaux, fin des compensations…) se passent
  en paramètre ou proviendront des autres documents du dossier.

## Résultat sur l'exemple de Bruges (réponses injectées, `examples/bruges/rapport.md`)

- Découpage : 13 VU, 6 CONSIDÉRANT, 43 articles et sous-articles, signature, 9 figures rattachées à leur article.
- 35 espèces, 7 impacts chiffrés, 19 mesures, 109 obligations, ramenées à 99 après fusion des redites.
- 14 échéances datées : planning et protocole EEE au 20/11/2025 ; plan de gestion, foncier, opérateurs et GéoMCE
  au 20/05/2026 ; bilans au 31/03 de 2029 à 2056 ; comité de suivi ; etc.
- Contradictions trouvées par règle : compte rendu des suivis au 31/03 (art. 14) contre 31/12 (art. 16.1) ;
  compte rendu des travaux compensatoires « dès leur achèvement » (art. 12.1) contre « au 31/03 de N+1 » (art. 16.1).
  La contradiction « bilans à 3 et 5 ans » contre « N+4, N+6 » relève de la passe LLM, et apparaît en
  ambiguïté sur l'obligation concernée.

## Limites connues

- Les réponses de référence de Bruges ont été rédigées à la main, par lecture de l'acte. Elles fixent le
  format attendu, mais ce n'est qu'**un** acte. Il faut 3 ou 4 autres arrêtés (autre préfecture, autorisation
  environnementale, arrêté modificatif) pour régler prompts et seuils.
- La fusion et la détection de contradictions par règle reposent sur la similarité des intitulés. La passe LLM
  sert de filet de sécurité.
- Une période avec deux fenêtres (« mi-mars à mi-mai **ou** septembre à mi-octobre ») est représentée par deux
  obligations : le schéma ne porte qu'une fenêtre par échéance.
- Le chaînage entre actes (arrêté modificatif qui remplace un article) est prévu dans le schéma (`actes_lies`),
  mais pas encore exploité.
