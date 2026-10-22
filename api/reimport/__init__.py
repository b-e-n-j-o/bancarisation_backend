"""Réimport d'un export KerERC : analyse, puis création d'un nouveau projet."""

VERSIONS = frozenset({"1.1"})
FORMAT = "kererc-export-projet"

# Colonnes jamais réinsérées (générées, dérivées, ou techniques d'une autre instance).
COLONNES_IGNOREES = frozenset({
    "geom_3857", "geom_4326", "geom_local", "surface_calculee_m2",
    "bbox_local", "geom_intersection",
})
COLONNES_AUTEUR = frozenset({"utilisateur_id", "cree_par"})

# (table, fichier, fk -> table source, tableaux d'uuid)
TABLES_IMPORT: list[tuple[str, str, dict[str, str], list[str]]] = [
    ("projets", "projets.csv", {}, []),
    ("projet_metadata", "projet_metadata.csv", {"projet_id": "projets"}, []),
    ("personnes", "personnes.csv", {}, []),
    ("action_fiche", "action_fiche.csv", {"projet_id": "projets"}, []),
    ("echeance", "echeance.csv", {"projet_id": "projets"}, []),
    ("unites_de_gestion_surf", "unites_de_gestion_surf.csv", {"projet_id": "projets"}, []),
    ("unites_de_gestion_lin", "unites_de_gestion_lin.csv", {"projet_id": "projets"}, []),
    ("unites_de_gestion_pct", "unites_de_gestion_pct.csv", {"projet_id": "projets"}, []),
    ("emprise_projet", "emprise_projet.csv", {"projet_id": "projets"}, []),
    ("occurrence", "occurrence.csv", {"projet_id": "projets", "echeance_id": "echeance", "responsable_id": "personnes"}, []),
    ("occurrence_finance", "occurrence_finance.csv", {"occurrence_id": "occurrence", "projet_id": "projets"}, []),
    ("budget_baseline", "budget_baseline.csv", {"projet_id": "projets"}, []),
    ("budget_mouvement", "budget_mouvement.csv", {"projet_id": "projets", "occurrence_id": "occurrence"}, []),
    ("documents", "documents.csv", {"projet_id": "projets", "occurrence_id": "occurrence"}, []),
    ("projet_contact", "projet_contact.csv", {"projet_id": "projets", "personne_id": "personnes"}, []),
    ("parcelles", "parcelles.csv", {"projet_id": "projets"}, []),
    ("droits_fonciers", "droits_fonciers.csv", {"parcelle_id": "parcelles", "titulaire_id": "personnes", "constituant_id": "personnes"}, []),
    ("droit_documents", "droit_documents.csv", {"droit_id": "droits_fonciers", "document_id": "documents"}, []),
    ("ug_surf_parcelles", "ug_surf_parcelles.csv", {"projet_id": "projets", "ug_id": "unites_de_gestion_surf", "parcelle_id": "parcelles"}, []),
    ("ug_lin_parcelles", "ug_lin_parcelles.csv", {"projet_id": "projets", "ug_id": "unites_de_gestion_lin", "parcelle_id": "parcelles"}, []),
    ("ug_pct_parcelles", "ug_pct_parcelles.csv", {"projet_id": "projets", "ug_id": "unites_de_gestion_pct", "parcelle_id": "parcelles"}, []),
    ("arrete", "arrete.csv", {"projet_id": "projets", "document_id": "documents"}, []),
    ("arrete_prescription", "arrete_prescription.csv", {"arrete_id": "arrete", "projet_id": "projets"}, []),
    ("prescription_couverture", "prescription_couverture.csv", {"prescription_id": "arrete_prescription", "echeance_id": "echeance", "projet_id": "projets"}, []),
    ("demande", "demande.csv", {"projet_id": "projets", "occurrence_id": "occurrence"}, []),
    ("demande_message", "demande_message.csv", {"demande_id": "demande", "projet_id": "projets"}, ["documents_ids"]),
    ("note_interne", "note_interne.csv", {"projet_id": "projets", "occurrence_id": "occurrence", "auteur_id": "personnes"}, []),
    ("action_etape", "action_etape.csv", {"projet_id": "projets", "occurrence_id": "occurrence"}, []),
    ("plan_cao", "plan_cao.csv", {"projet_id": "projets", "document_id": "documents"}, []),
    ("plan_cao_groupe", "plan_cao_groupe.csv", {"plan_id": "plan_cao", "projet_id": "projets"}, []),
    ("plan_cao_calque", "plan_cao_calque.csv", {"plan_id": "plan_cao", "projet_id": "projets", "groupe_id": "plan_cao_groupe"}, []),
    ("plan_cao_entite", "plan_cao_entite.csv", {"plan_id": "plan_cao", "projet_id": "projets"}, []),
    ("rapport_bilan", "rapport_bilan.csv", {"projet_id": "projets", "document_id": "documents"}, []),
]

NON_REPRIS = (
    "projet_acces : les organisations partenaires sont listées dans le rapport, "
    "à recréer à la main.",
    "Comptes utilisateurs, organisations et journal des exports.",
    "Notes internes des autres organisations : elles ne sont pas dans l'export.",
    "Onglet Suivi satellite.",
)
