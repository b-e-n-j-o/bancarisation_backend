"""
extracteurs — importer ce package peuple le REGISTRE.

Un nouvel extracteur = un module ici + une ligne d'import. Rien d'autre à
toucher dans la chaîne : le planificateur le découvrira par ses rôles déclarés.

À écrire ensuite, dans cet ordre de valeur :
  · prescriptions_arrete   (pdf, rôle arrete)          → Kind.prescription
  · statut_realisation_xlsx(xlsx, rôle statut_realisation) → Kind.fait_realise
  · decompte_xlsx          (xlsx, rôle decompte_facturation) → Kind.fait_realise
  · calendrier_recap       (pdf/xlsx, rôle calendrier_previsionnel)
       → NE produit PAS d'échéance : sert de contrôle croisé contre les règles
         extraites du narratif. Un écart = signal de qualité d'extraction.
  · ug_table               (pdf/xlsx, rôle carto_ug)    → Kind.unite_gestion
  · ug_couche_sig          (sig, rôle carto_ug)         → Kind.unite_gestion  ✅
"""

from . import budget_xlsx, plan_gestion, sig_ug  # noqa: F401

__all__ = ["budget_xlsx", "plan_gestion", "sig_ug"]
