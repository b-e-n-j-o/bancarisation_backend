"""Classeur projet.xlsx généré à la volée, listes synchronisées avec les référentiels."""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill, Protection
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from api.reimport.modele.listes import (
    CATEGORIES_DOC,
    DISPOSITIFS,
    PROCEDURES,
    ROLES,
    VERSION_MODELE,
    libelles_thema,
)
from api.reimport.modele.normaliser import MOIS_LIBELLES

GRAS = Font(bold=True)
OBLIGATOIRE = Font(bold=True, color="9A3412")
EXEMPLE = Font(italic=True, color="64748B")
FOND_EXEMPLE = PatternFill("solid", fgColor="F1F5F9")


def generer_classeur(roles: list[tuple[str, str]] | None = None) -> bytes:
    roles = roles or ROLES
    wb = Workbook()
    listes = wb.active
    listes.title = "_listes"
    _ecrire_listes(listes, roles)
    listes.sheet_state = "hidden"

    meta = wb.create_sheet("_meta")
    meta["A1"] = "modele"
    meta["B1"] = "kererc-import-projet"
    meta["A2"] = "version"
    meta["B2"] = VERSION_MODELE
    meta["A3"] = "genere_le"
    meta["B3"] = datetime.now().isoformat(timespec="seconds")
    meta.sheet_state = "hidden"

    notice = wb.create_sheet("Notice", 0)
    _notice(notice)

    projet = wb.create_sheet("Projet")
    _deux_colonnes(projet, [
        ("Nom", True, "Nom du site, tel qu'il apparaîtra dans KerERC."),
        ("Commune", True, "Commune principale."),
        ("Département", False, "Numéro ou nom."),
        ("Référence interne", False, "Votre cote, libre."),
        ("Procédure", False, "Liste."),
        ("Type de dispositif", False, "Obligation ou site de crédits."),
        ("Référence de la décision", False, "Numéro de l'arrêté ou de l'autorisation."),
        ("Date de la décision", False, "JJ/MM/AAAA."),
        ("Référence étude d'impact", False, ""),
        ("Durée (années)", True, "Durée de l'obligation, en années."),
        ("Année de début (année N)", True, "Première année du plan. Pivot des récurrences."),
        ("Description", False, ""),
    ], [("Nom", "EXEMPLE")])
    _liste(projet, "E", f"=_listes!$A$2:$A${1 + len(PROCEDURES)}", "Procédure")
    _liste(projet, "E", f"=_listes!$B$2:$B${1 + len(DISPOSITIFS)}", "Type de dispositif")

    mesures = wb.create_sheet("Mesures")
    _tableau(mesures, [
        ("Code", True, "Unique. Exemple : ES1."),
        ("Libellé", True, "Intitulé de la mesure."),
        ("Catégorie Théma", False, "Code du catalogue Théma, ou autre."),
        ("UG concernées", False, "Codes de l'onglet UG, séparés par ;"),
    ], ["EXEMPLE", "Création de mare", "autre", "ug1"])
    _liste(mesures, "C", f"=_listes!$C$2:$C${1 + len(libelles_thema())}")

    ug = wb.create_sheet("UG")
    _tableau(ug, [
        ("Code", True, "Unique. Exemple : ug1. Les espaces sont ignorés."),
        ("Libellé", False, "Nom lisible."),
        ("Type", True, "surfacique, linéaire ou ponctuelle."),
    ], ["EXEMPLE", "Mare nord", "surfacique"])
    _liste(ug, "C", "=_listes!$D$2:$D$4")

    plan = wb.create_sheet("Actions – plan")
    _tableau(plan, [
        ("Réf. action", True, "Code unique choisi par vous. Exemple : FAU1."),
        ("Mesure", True, "Code de l'onglet Mesures."),
        ("Libellé", True, ""),
        ("UG", True, "Codes séparés par ;"),
        ("Mois de début", True, "Mois en toutes lettres."),
        ("Mois de fin", True, "Si le mois de fin précède le mois de début, la fenêtre passe le nouvel an."),
        ("Récurrence", True, "ponctuelle, annuelle, tous les N ans, années listées."),
        ("N", False, "Obligatoire pour « tous les N ans »."),
        ("Années", False, "Pour « années listées », séparées par ;. Pour « ponctuelle », une seule année."),
        ("Première année", False, "Défaut : année de début du projet."),
        ("Dernière année", False, "Défaut : année de début + durée − 1."),
        ("Coût unitaire HT", False, "Montant prévu de chaque occurrence."),
        ("Prestataire", False, "Nom, tel que vous le connaissez."),
    ], ["EXEMPLE", "ES1", "Fauche", "ug1", "juin", "septembre", "annuelle", "", "", 2024, 2033, 1200, ""])
    _liste(plan, "E", "=_listes!$E$2:$E$13")
    _liste(plan, "F", "=_listes!$E$2:$E$13")
    _liste(plan, "G", "=_listes!$F$2:$F$5")

    cal = wb.create_sheet("Actions – calendrier")
    _tableau(cal, [
        ("Réf. action", False, "Code du plan, ou vide pour une action libre."),
        ("Mesure", False, "Obligatoire si Réf. action est vide."),
        ("Libellé", False, "Obligatoire si Réf. action est vide."),
        ("UG", False, "Défaut : celles du plan."),
        ("Année", True, ""),
        ("Mois de début", True, "Défaut : celui du plan."),
        ("Mois de fin", True, ""),
        ("Statut", True, "à confirmer, planifiée, en cours, réalisée, repoussée."),
        ("Date de réalisation", False, "JJ/MM/AAAA."),
        ("Montant prévu HT", False, ""),
        ("Montant engagé", False, ""),
        ("Montant réalisé", False, ""),
        ("Prestataire", False, "Défaut : celui du plan."),
        ("Commentaire", False, ""),
    ], ["EXEMPLE", "", "", "", 2023, "juin", "juillet", "réalisée", "15/07/2023", 1200, 1200, 980, "", "Fait en régie"])
    _liste(cal, "F", "=_listes!$E$2:$E$13")
    _liste(cal, "G", "=_listes!$E$2:$E$13")
    _liste(cal, "H", "=_listes!$G$2:$G$6")

    contacts = wb.create_sheet("Contacts")
    _tableau(contacts, [
        ("Nom", True, ""),
        ("Organisation", False, "Structure, en texte libre."),
        ("Rôle", True, "Liste des rôles KerERC."),
        ("Email", False, ""),
        ("Téléphone", False, ""),
    ], ["EXEMPLE", "Commune de Ploemel", "Maître d'ouvrage", "contact@exemple.fr", ""])
    _liste(contacts, "C", f"=_listes!$H$2:$H${1 + len(roles)}")

    _ = CATEGORIES_DOC
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _ecrire_listes(feuille, roles: list[tuple[str, str]]) -> None:
    colonnes = {
        "A": PROCEDURES,
        "B": [lib for _code, lib in DISPOSITIFS],
        "C": libelles_thema(),
        "D": ["surfacique", "linéaire", "ponctuelle"],
        "E": MOIS_LIBELLES,
        "F": ["ponctuelle", "annuelle", "tous les N ans", "années listées"],
        "G": ["à confirmer", "planifiée", "en cours", "réalisée", "repoussée"],
        "H": [lib for _code, lib in roles],
    }
    for col, valeurs in colonnes.items():
        feuille[f"{col}1"] = col
        for i, valeur in enumerate(valeurs, start=2):
            feuille[f"{col}{i}"] = valeur


def _notice(feuille) -> None:
    lignes = [
        "Importer un projet KerERC depuis ce classeur",
        "",
        "Le dossier s'appelle comme vous voulez. Il contient projet.xlsx, un dossier sig/ et un dossier documents/.",
        "sig/ reçoit les shapefiles (avec .prj), GeoPackage ou GeoJSON. Un champ doit porter le code UG (code_ug, ou celui que vous indiquerez).",
        "Une couche nommée emprise devient l'emprise du projet.",
        "documents/ : un sous-dossier par catégorie (arrete, plan_gestion, financier…). Le nom de fichier peut commencer par AAAA-MM-JJ.",
        "",
        "Deux façons de décrire les actions.",
        "L'onglet Actions – plan décrit la règle (chaque année, tous les N ans…). KerERC en déduit les occurrences.",
        "L'onglet Actions – calendrier décrit une année précise : un passé réalisé, un décompte, un ajustement.",
        "S'il existe au moins une ligne de calendrier pour une action et une année, elle remplace les occurrences générées pour ce couple.",
        "Une ligne de calendrier sans référence d'action est une action libre.",
        "",
        "La ligne dont la première cellule vaut EXEMPLE est ignorée. Les en-têtes se reconnaissent par leur libellé.",
        "Les champs marqués en orange sont obligatoires.",
    ]
    for i, ligne in enumerate(lignes, start=1):
        feuille.cell(i, 1, ligne)
    feuille.column_dimensions["A"].width = 120
    feuille.cell(1, 1).font = GRAS


def _deux_colonnes(feuille, cles, exemples) -> None:
    feuille["A1"] = "Clé"
    feuille["B1"] = "Valeur"
    feuille["A1"].font = GRAS
    feuille["B1"].font = GRAS
    feuille.freeze_panes = "A2"
    feuille.column_dimensions["A"].width = 36
    feuille.column_dimensions["B"].width = 42
    par_cle = dict(exemples)
    for i, (cle, obligatoire, aide) in enumerate(cles, start=2):
        cell = feuille.cell(i, 1, cle + (" *" if obligatoire else ""))
        cell.font = OBLIGATOIRE if obligatoire else GRAS
        if aide:
            cell.comment = Comment(aide, "KerERC")
        if cle in par_cle:
            valeur = feuille.cell(i, 2, par_cle[cle])
            valeur.font = EXEMPLE
            valeur.fill = FOND_EXEMPLE
    if "Nom" in par_cle:
        feuille["B2"] = "EXEMPLE"


def _tableau(feuille, colonnes, exemple) -> None:
    feuille.freeze_panes = "A2"
    feuille.auto_filter.ref = f"A1:{get_column_letter(len(colonnes))}1"
    for i, (libelle, obligatoire, aide) in enumerate(colonnes, start=1):
        cell = feuille.cell(1, i, libelle + (" *" if obligatoire else ""))
        cell.font = OBLIGATOIRE if obligatoire else GRAS
        if aide:
            cell.comment = Comment(aide, "KerERC")
        feuille.column_dimensions[get_column_letter(i)].width = max(16, len(libelle) + 4)
    for i, valeur in enumerate(exemple, start=1):
        cell = feuille.cell(2, i, valeur)
        cell.font = EXEMPLE
        cell.fill = FOND_EXEMPLE
    feuille.protection = Protection(sheet=False)


def _liste(feuille, colonne: str, formule: str, cle: str | None = None) -> None:
    validation = DataValidation(type="list", formula1=formule, allow_blank=True)
    feuille.add_data_validation(validation)
    if cle is None:
        validation.add(f"{colonne}2:{colonne}500")
        return
    for row in range(2, 40):
        if str(feuille.cell(row, 1).value or "").startswith(cle):
            validation.add(f"B{row}")
            return


_ = date
