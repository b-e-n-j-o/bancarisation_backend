"""
familles.py — La table de droits d'écriture du système.

C'est la pièce la plus structurante de tout le pipeline : elle dit, pour chaque
rôle documentaire, CE QUE LE DOCUMENT A LE DROIT D'ÉCRIRE dans le modèle.

Sans elle, un décompte de facturation 2024 finit par décaler une récurrence, et
un arrêté préfectoral se met à produire des occurrences de calendrier — deux
bugs qu'on ne détecte jamais en relecture, seulement six mois plus tard chez le
client.

Rappel de la décision produit : l'arrêté n'est PAS une source de calendrier.
Il fixe des obligations (checklist de référence) ; c'est le BE qui pilote les
actions datées. Les prescriptions sont reliées aux échéances en couverture n:n.
"""

from __future__ import annotations

from enum import Enum


class Famille(str, Enum):
    regles = "regles"            # → action_fiche + echeance (moulinées par le semoir)
    obligations = "obligations"  # → arrete_prescription (jamais d'occurrence)
    cout = "cout"                # → ligne_budget (jamais d'échéance seul)
    realise = "realise"          # → statuts + montants engagé/réalisé (jamais de règle)
    contexte = "contexte"        # → paramètres du dossier, UG, glossaire
    inconnu = "inconnu"


class RoleDoc(str, Enum):
    plan_gestion = "plan_gestion"
    fiches_actions = "fiches_actions"
    calendrier_previsionnel = "calendrier_previsionnel"

    arrete = "arrete"
    convention_ore = "convention_ore"
    autorisation_environnementale = "autorisation_environnementale"

    estimation_budget = "estimation_budget"
    planning_financier = "planning_financier"
    devis_marche = "devis_marche"

    decompte_facturation = "decompte_facturation"
    statut_realisation = "statut_realisation"
    compte_rendu_suivi = "compte_rendu_suivi"

    diagnostic_ecologique = "diagnostic_ecologique"
    carto_ug = "carto_ug"
    glossaire_codes = "glossaire_codes"

    autre = "autre"


FAMILLE_DE_ROLE: dict[RoleDoc, Famille] = {
    RoleDoc.plan_gestion: Famille.regles,
    RoleDoc.fiches_actions: Famille.regles,
    RoleDoc.calendrier_previsionnel: Famille.regles,
    RoleDoc.arrete: Famille.obligations,
    RoleDoc.convention_ore: Famille.obligations,
    RoleDoc.autorisation_environnementale: Famille.obligations,
    RoleDoc.estimation_budget: Famille.cout,
    RoleDoc.planning_financier: Famille.cout,
    RoleDoc.devis_marche: Famille.cout,
    RoleDoc.decompte_facturation: Famille.realise,
    RoleDoc.statut_realisation: Famille.realise,
    RoleDoc.compte_rendu_suivi: Famille.realise,
    RoleDoc.diagnostic_ecologique: Famille.contexte,
    RoleDoc.carto_ug: Famille.contexte,
    RoleDoc.glossaire_codes: Famille.contexte,
    RoleDoc.autre: Famille.inconnu,
}

# Ordre d'exécution des jobs. Le contexte d'abord (T0, UG, codes mesure), puis
# les règles (le calendrier n'existe pas avant), puis le coût qui s'y rattache,
# puis les obligations, puis le réalisé qui se rejoue par-dessus.
ORDRE_FAMILLE: dict[Famille, int] = {
    Famille.contexte: 0,
    Famille.regles: 1,
    Famille.cout: 2,
    Famille.obligations: 3,
    Famille.realise: 4,
    Famille.inconnu: 9,
}


def famille(role: RoleDoc) -> Famille:
    return FAMILLE_DE_ROLE.get(role, Famille.inconnu)


DESCRIPTION_ROLES = {
    RoleDoc.plan_gestion: "Plan de gestion : diagnostic, unités de gestion, fiches-actions avec modalités et fréquences d'intervention.",
    RoleDoc.fiches_actions: "Fiches-actions isolées (hors plan de gestion complet) décrivant une opération, sa fréquence, ses UG.",
    RoleDoc.calendrier_previsionnel: "Tableau récapitulatif actions × années (frise, planning prévisionnel). Sert de contrôle croisé.",
    RoleDoc.arrete: "Arrêté préfectoral : prescriptions, obligations de suivi, délais, garanties financières.",
    RoleDoc.convention_ore: "Convention, ORE, acte notarié fixant des engagements de long terme.",
    RoleDoc.autorisation_environnementale: "Autorisation ou dérogation espèces protégées, étude d'impact.",
    RoleDoc.estimation_budget: "Estimation de coûts : prestations, coûts unitaires, HT/TVA/TTC, prestataires.",
    RoleDoc.planning_financier: "Matrice actions × années en euros ou en occurrences, récapitulatif par prestataire.",
    RoleDoc.devis_marche: "Devis, marché, bon de commande.",
    RoleDoc.decompte_facturation: "Facturation réelle : acomptes, décomptes, avoirs, prestations non réalisées.",
    RoleDoc.statut_realisation: "État d'avancement par action et par année (réalisé / projeté / supprimé).",
    RoleDoc.compte_rendu_suivi: "Compte rendu de visite, rapport de suivi annuel, bilan écologique.",
    RoleDoc.diagnostic_ecologique: "État initial, inventaires, habitats, espèces.",
    RoleDoc.carto_ug: "Description ou table des unités de gestion (surfaces, identifiants, parcelles).",
    RoleDoc.glossaire_codes: "Nomenclature des codes mesure (MG, SE, TU, TE…), légende, abréviations.",
    RoleDoc.autre: "Aucun des rôles ci-dessus.",
}