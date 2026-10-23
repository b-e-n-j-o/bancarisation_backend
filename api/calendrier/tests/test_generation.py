"""Régénération : figées intactes, conditionnelles vides, idempotence. Sans base."""

from api.calendrier.generation import appliquer_memoire, planifier


def _echeance(**extra):
    base = {
        "id": "ech-1",
        "origine": "ia",
        "statut": "a_confirmer",
        "code_operation": "RG1",
        "libelle": "Griffage",
        "type_operation": "TU",
        "lib_thema": "autre",
        "ug_ids": ["ug1"],
        "fenetre_debut": "03-01",
        "fenetre_fin": "04-30",
        "fenetre_traverse_nouvel_an": False,
        "confiance": 0.8,
        "recurrence": {
            "type": "paliers",
            "annee_N": 2025,
            "duree_ans": 4,
            "paliers": [{"debut": 0, "fin": 4, "pas": 1}],
            "annees_explicites": [],
        },
    }
    base.update(extra)
    return base


def _occ(annee, *, fige=False, realisation=None, titre="Griffage", identifiant=None):
    return {
        "id": identifiant or f"occ-{annee}",
        "annee": annee,
        "fige": fige,
        "date_realisation": realisation,
        "titre": titre,
        "code": "RG 1",
        "categorie": "TU",
        "lib_thema": "autre",
        "ug_ids": ["ug1"],
        "mois_debut": 3,
        "mois_fin": 4,
        "traverse_nouvel_an": False,
        "statut": "a_confirmer",
        "origine": "ia",
    }


def _signature(occurrences):
    return sorted(
        (o["annee"], o["titre"], o["statut"], bool(o["fige"]), o.get("date_realisation"))
        for o in occurrences
    )


def test_regle_modifiee_remplace_les_non_figees():
    echeance = _echeance()
    existantes = [
        _occ(2025, fige=True, titre="ancre figée"),
        _occ(2026, fige=True, realisation="2026-03-12", titre="fait main"),
        _occ(2027),
        _occ(2028),
    ]
    echeance["recurrence"] = {
        "type": "paliers",
        "annee_N": 2025,
        "duree_ans": 4,
        "paliers": [{"debut": 0, "fin": 4, "pas": 2}],
        "annees_explicites": [],
    }
    rapport = planifier(echeance, existantes)
    assert "occ-2028" in rapport.supprimees
    assert "occ-2027" not in rapport.supprimees
    assert "occ-2026" in rapport.conservees
    assert 2025 in rapport.annees_en_conflit
    assert 2025 not in [c["annee"] for c in rapport.creees]
    assert 2029 in [c["annee"] for c in rapport.creees]
    suivant = appliquer_memoire(existantes, rapport)
    annees = sorted(o["annee"] for o in suivant)
    assert annees == [2025, 2026, 2027, 2029]
    hors_regle = next(o for o in suivant if o["annee"] == 2026)
    assert hors_regle["titre"] == "fait main"
    assert hors_regle["date_realisation"] == "2026-03-12"
    ancre = next(o for o in suivant if o["annee"] == 2025)
    assert ancre["titre"] == "ancre figée"


def test_dependant_evenement_supprime_les_non_figees():
    echeance = _echeance(recurrence={
        "type": "dependant_evenement",
        "annee_N": 2025,
        "regle_source": "si pertes",
        "fenetre_annees": [1, 3],
    })
    existantes = [_occ(2025), _occ(2026, fige=True)]
    rapport = planifier(echeance, existantes)
    assert rapport.annees == []
    assert rapport.creees == []
    assert "occ-2025" in rapport.supprimees
    assert "occ-2026" in rapport.conservees
    reste = appliquer_memoire(existantes, rapport)
    assert [o["annee"] for o in reste] == [2026]


def test_idempotence():
    echeance = _echeance(statut="validee")
    premier = planifier(echeance, [])
    assert [c["annee"] for c in premier.creees] == [2025, 2026, 2027, 2028, 2029]
    assert all(c["statut"] == "planifie" for c in premier.creees)
    etat = appliquer_memoire([], premier)
    second = planifier(echeance, etat)
    assert second.creees == []
    assert second.supprimees == []
    assert second.mises_a_jour == []
    assert _signature(appliquer_memoire(etat, second)) == _signature(etat)


def test_occurrence_realisee_sans_drapeau_est_conservee():
    echeance = _echeance()
    existantes = [_occ(2025, realisation="2025-04-01", titre="terrain")]
    rapport = planifier(dict(echeance), existantes)
    assert "occ-2025" in rapport.conservees
    assert 2025 in rapport.annees_en_conflit
    assert 2025 not in [c["annee"] for c in rapport.creees]
    assert rapport.supprimees == []
