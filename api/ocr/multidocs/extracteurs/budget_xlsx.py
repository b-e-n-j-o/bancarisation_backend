"""
budget_xlsx.py — Extraction budgétaire, une table à la fois.

CHANGEMENT NOTABLE PAR RAPPORT AU PROTO : la passe 1 (cartographie LLM du
classeur) DISPARAÎT. Le normaliseur tableur.py la fait déjà, de façon
déterministe, gratuite et exacte : bandes de lignes, feuilles, fusions,
marqueurs [TOTAL?]. Le triage attribue le rôle. Il ne reste que l'extraction —
et elle se fait table par table, pas classeur par classeur.

C'est la correction directe de ton échec de premier test (>32k tokens de
réflexion, modèle en boucle sur des contradictions) : avec une table à la fois,
il n'y a plus de contradiction inter-tables à arbitrer dans le prompt. Les
doublons voulus (une prestation présente en coût unitaire ici et en déroulé
annuel là) sortent en deux claims distincts, comme prévu — la réconciliation
est un autre étage.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..utils.claims import Claim, Kind, enregistrer_payload
from ..utils.familles import RoleDoc
from ..utils.registre import extracteur

if TYPE_CHECKING:
    from ..utils.plan import Contexte, Job

VERSION = "0.2.0"

SYSTEM_PROMPT = """Tu extrais les lignes budgétaires d'UNE table d'un classeur d'estimation \
fourni par un bureau d'études pour des mesures compensatoires environnementales.

La table t'est donnée en markdown. La colonne "ligne" porte le numéro de ligne Excel d'origine \
et, le cas échéant, des annotations :
  ⟨montants communs aux lignes X-Y⟩  la cellule de montant est fusionnée et couvre les lignes X à Y : \
le montant vaut pour le GROUPE. Extrais-le sur cette ligne d'ancrage et renseigne montant_partage_avec.
  ⟨montants portés par la ligne X⟩   la ligne n'a pas de montant propre : laisse montant_ht à null.
  ⟨col. A-D⟩                          le montant couvre cette plage de colonnes, une seule fois.
  [TOTAL?]                            la ligne contient une formule de somme : c'est très \
probablement une agrégation.

RÈGLES :
1. Une ligne extraite par prestation budgétée. Reprends les libellés tels quels, sans reformuler.
2. NE RÉCONCILIE RIEN. Tu ne vois qu'une table ; d'autres tables du même classeur peuvent décrire \
les mêmes prestations autrement. Ce n'est pas ton problème : extrais ce que TU vois, tel quel. \
Ne déduplique pas, ne fusionne pas, n'arbitre aucune contradiction.
3. Toute ligne d'agrégation (sous-total, total, récap par prestataire, total annuel) sort avec \
est_total=true, jamais mélangée au détail. Les [TOTAL?] sont des indices ; certaines agrégations \
sont saisies en dur et n'en ont pas : identifie-les au libellé.
4. Ne calcule JAMAIS un montant. Ne reporte que des valeurs présentes. Valeur ambiguë ou absente \
→ null + le nom du champ dans champs_a_confirmer.
5. Colonnes d'années → remplis annees pour la ligne correspondante.
6. code_mesure uniquement s'il figure dans le document. Ne l'invente jamais.
7. Montants négatifs (avoirs, prestations non réalisées) : reporte-les tels quels et renseigne \
statut_reel si le document l'indique.
8. Recopie dans totaux_declares les totaux affichés, avec leur périmètre : ils servent à un \
contrôle croisé programmatique en aval.
{regles_matching}"""

REGLES_MATCHING = """
9. RÉFÉRENTIEL D'ACTIONS déjà extrait du plan de gestion (code → libellé) :
{referentiel}
Si une ligne de détail correspond de façon FIABLE à une action du référentiel (par le code mesure, \
à défaut par similarité de libellé), renseigne action_associee. Au moindre doute : \
action_associee=null et "action_associee" dans champs_a_confirmer. Ne force jamais une association.
"""

USER_PROMPT = """{contexte}

Table {locator} du classeur {fichier}.

{table}"""


@extracteur(
    nom="budget_table_xlsx",
    version=VERSION,
    roles=(
        RoleDoc.estimation_budget,
        RoleDoc.planning_financier,
        RoleDoc.devis_marche,
    ),
    formats=("xlsx", "xlsm"),
    produit=(Kind.ligne_budget,),
    description="Extrait les lignes budgétaires d'un classeur d'estimation, une "
                "table logique par appel (fan-out).",
    cout="moyen",
    fan_out=True,
)
def budget_table_xlsx(job: "Job", ctx: "Contexte") -> list[Claim]:
    from ...extractions.extract_budget import ExtractionBudget
    from ...mistral_client import DEFAULT_MODEL, extraire_structure

    enregistrer_payload(Kind.ligne_budget, _payload_ligne())

    regles = ""
    if ctx.referentiel_actions:
        regles = REGLES_MATCHING.format(
            referentiel=json.dumps(ctx.referentiel_actions, ensure_ascii=False, indent=2)
        )
    system = SYSTEM_PROMPT.format(regles_matching=regles)

    claims: list[Claim] = []
    for locator in job.locators:
        bloc = ctx.document.bloc(locator)
        if bloc is None or bloc.vide:
            continue

        resultat: ExtractionBudget = extraire_structure(
            system_prompt=system,
            user_prompt=USER_PROMPT.format(
                contexte=ctx.entete_contexte(),
                locator=locator,
                fichier=ctx.document.nom_fichier,
                table=bloc.texte,
            ),
            result_type=ExtractionBudget,
            etiquettes=f"BUDGET/{locator}",
            debug_dir=ctx.debug_dir,
            debug_prefixe="budget",
            model=DEFAULT_MODEL,
            effort="medium",  # une table seule ne justifie pas effort=high
            max_tokens=16000,
            utiliser_schema=False,
            compteur=ctx.compteur,
            schema_name="extraction_budget_table",
        )

        ancre = ctx.document.ancre(locator)
        for ligne in resultat.lignes:
            claims.append(
                Claim.depuis(
                    ligne,
                    kind=Kind.ligne_budget,
                    doc_id=ctx.document.doc_id,
                    extracteur="budget_table_xlsx",
                    version=VERSION,
                    ancres=[ancre],
                    confiance=getattr(ligne, "confiance", 1.0),
                    champs_a_confirmer=list(getattr(ligne, "champs_a_confirmer", []) or []),
                    cle_locale=getattr(ligne, "code_mesure", None),
                )
            )
        # Les totaux déclarés voyagent avec les lignes : le contrôle croisé
        # (somme du détail vs total affiché) se fait à la réconciliation, quand
        # toutes les tables du classeur sont revenues.
        for total in resultat.totaux_declares:
            claims.append(
                Claim.depuis(
                    total,
                    kind=Kind.ligne_budget,
                    doc_id=ctx.document.doc_id,
                    extracteur="budget_table_xlsx",
                    version=VERSION,
                    ancres=[ancre],
                    avertissements=["total_declare"],
                )
            )
    return claims


def _payload_ligne():
    from ...extractions.extract_budget import LigneBudget

    return LigneBudget