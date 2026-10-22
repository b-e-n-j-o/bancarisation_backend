"""Fusion, années listées, action libre, UG sans géométrie. Sans base."""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.ocr.calcul_occurrences import generer
from api.ocr.models import Echeance, FenetreIntervention, Recurrence, TypeRecurrence
from api.reimport.analyse import extraire_zip
from api.reimport.modele.lecteur_sig import coordonnees_hors_wgs84, loin_de_france
from api.reimport.modele.traduction import construire
from api.reimport.router import _preparer


def _dossier(**extra):
    base = {
        "projet": {
            "Nom": "Etang", "Commune": "Ploemel",
            "Durée (années)": 20, "Année de début (année N)": 2020,
        },
        "mesures": [{"_ligne": 2, "Code": "ES1", "Libellé": "Mare", "Catégorie Théma": "autre", "UG concernées": "ug1"}],
        "ug": [{"_ligne": 2, "Code": "ug1", "Libellé": "Mare nord", "Type": "surfacique"}],
        "plan": [],
        "calendrier": [],
        "contacts": [],
    }
    base.update(extra)
    return base


def test_action_libre_garde_sa_mesure():
    jeu, constats, _resume = construire(
        _dossier(calendrier=[{
            "_ligne": 4, "Réf. action": "", "Mesure": "ES1", "Libellé": "Visite",
            "UG": "ug1", "Année": 2024, "Mois de début": "mai", "Mois de fin": "juin",
            "Statut": "planifiée",
        }]),
        "org", "imp", {}, False,
    )
    echeance = jeu.tables["echeance"][0]
    occurrence = jeu.tables["occurrence"][0]
    assert echeance["action_cle"] == "ES1"
    assert echeance["recurrence"]["type"] == "ponctuel"
    assert occurrence["echeance_id"] == echeance["id"]
    assert not any(c.niveau == "erreur" for c in constats)


def test_annees_listees_trois_occurrences():
    echeance = Echeance(
        id="SUIVI",
        code_operation="SUIVI",
        type_operation="SE",
        libelle="Suivi",
        ug_ids=["ug1"],
        recurrence=Recurrence(
            type=TypeRecurrence.explicite,
            ancrage_annee=2025,
            annee_fin=2035,
            annees=[2025, 2030, 2035],
        ),
        fenetre_intervention=FenetreIntervention(debut="06-01", fin="09-01"),
        confiance=1,
    )
    produits, non_placables = generer([echeance], annee_fin=2035)
    assert non_placables == []
    assert [o.annee for o in produits] == [2025, 2030, 2035]

    jeu, _constats, resume = construire(
        _dossier(plan=[{
            "_ligne": 2, "Réf. action": "SUIVI", "Mesure": "ES1", "Libellé": "Suivi",
            "UG": "ug1", "Mois de début": "juin", "Mois de fin": "septembre",
            "Récurrence": "années listées", "Années": "2025 ; 2030 ; 2035",
            "Première année": 2025, "Dernière année": 2035,
        }]),
        "org", "imp", {}, False,
    )
    assert jeu.tables["echeance"][0]["recurrence"]["type"] == "explicite"
    assert sorted(o["annee"] for o in jeu.tables["occurrence"]) == [2025, 2030, 2035]
    assert resume["occurrences_generees"] == 3


def test_fusion_remplace_l_annee_du_calendrier():
    jeu, _c, resume = construire(
        _dossier(plan=[{
            "_ligne": 2, "Réf. action": "FAU1", "Mesure": "ES1", "Libellé": "Fauche",
            "UG": "ug1", "Mois de début": "juin", "Mois de fin": "septembre",
            "Récurrence": "annuelle", "Première année": 2020, "Dernière année": 2022,
            "Coût unitaire HT": 100,
        }], calendrier=[{
            "_ligne": 3, "Réf. action": "FAU1", "Année": 2021,
            "Mois de début": "juin", "Mois de fin": "juillet", "Statut": "réalisée",
            "Date de réalisation": "15/07/2021", "Montant prévu HT": 80,
        }]),
        "org", "imp", {}, False,
    )
    annees = sorted(o["annee"] for o in jeu.tables["occurrence"])
    assert annees == [2020, 2021, 2022]
    assert resume["occurrences_generees"] == 2
    assert resume["occurrences_remplacees"] == 1
    realisee = next(o for o in jeu.tables["occurrence"] if o["annee"] == 2021)
    assert realisee["statut"] == "realise"


def test_ug_sans_geometrie_avertit_sans_bloquer():
    jeu, constats, resume = construire(_dossier(), "org", "imp", {}, False)
    assert resume["ug_sans_geometrie"] == 1
    assert not any(c.niveau == "erreur" for c in constats)
    assert any("n'apparaîtra pas sur la carte" in c.message for c in constats)
    assert jeu.tables.get("unites_de_gestion_surf", []) == []
    assert "ug1" in jeu.tables["action_fiche"][0]["ug_ids"]


def test_lambert_sans_crs_nest_pas_du_wgs84():
    assert coordonnees_hors_wgs84(150_000, 6_700_000, 250_000, 6_800_000)
    assert not coordonnees_hors_wgs84(-4.5, 47.6, -4.2, 47.8)
    assert loin_de_france(-40_000_000, -10_000_000, -39_000_000, -9_000_000)


def test_zip_refuse_un_chemin_parent(tmp_path: Path):
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("../secret.txt", b"non")
    with pytest.raises(RuntimeError, match="Chemin refusé"):
        extraire_zip(tampon.getvalue(), tmp_path)


def test_zip_refuse_trop_de_fichiers(tmp_path: Path):
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        for i in range(4001):
            archive.writestr(f"f{i}.txt", b"a")
    with pytest.raises(RuntimeError, match="trop de fichiers"):
        extraire_zip(tampon.getvalue(), tmp_path)


def test_xlsm_refuse():
    with pytest.raises(HTTPException):
        _preparer(b"PK", "piege.xlsm")
