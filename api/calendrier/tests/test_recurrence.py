"""expand, to_phrase, convertir — sans base."""

import pytest

from api.calendrier.recurrence import RecurrenceError, convertir, expand, to_phrase


def _paliers(*paliers, annee=2025, duree=30, explicites=None):
    return {
        "type": "paliers",
        "ancrage": "N",
        "annee_N": annee,
        "paliers": [
            {"debut": a, "fin": b, "pas": p, **({"pas_max": mx} if mx is not None else {})}
            for a, b, p, mx in paliers
        ],
        "annees_explicites": explicites or [],
        "duree_ans": duree,
        "regle_source": "",
    }


def test_expand_paliers_deux_segments():
    regle = _paliers((0, 5, 1, None), (10, None, 5, None))
    assert expand(regle) == [
        2025, 2026, 2027, 2028, 2029, 2030,
        2035, 2040, 2045, 2050, 2055,
    ]


def test_expand_explicite_relatif_et_absolu():
    relatif = {
        "type": "explicite",
        "annee_N": 2025,
        "annees_explicites": [0, 2, 2],
    }
    assert expand(relatif) == [2025, 2027]
    absolu = {"type": "explicite", "ancrage_annee": 2025, "annees": [2026, 2025, 2030]}
    assert expand(absolu) == [2025, 2026, 2030]


def test_expand_continue_et_sans_dates():
    assert expand({"type": "continue", "annee_N": 2025, "duree_ans": 2}) == [2025, 2026, 2027]
    assert expand({"type": "dependant_evenement", "regle_source": "si pertes"}) == []
    assert expand({"type": "non_precisee"}) == []


def test_expand_exige_annee_n():
    with pytest.raises(RecurrenceError, match="annee_N"):
        expand({"type": "paliers", "paliers": [{"debut": 0, "fin": 1, "pas": 1}]})


def test_expand_pas_max_ne_change_pas_les_annees():
    regle = _paliers((3, None, 2, 3), duree=9)
    assert expand(regle) == [2028, 2030, 2032, 2034]


def test_phrase_paliers_puis_quinquennal():
    regle = _paliers((0, 5, 1, None), (10, None, 5, None))
    assert to_phrase(regle, "06-01", "07-31") == (
        "Chaque année de N à N+5, puis tous les 5 ans de N+10 à N+30 — juin à juillet"
    )


def test_phrase_annuel_decale_et_fourchette():
    assert to_phrase(_paliers((1, None, 1, None)), "03-01", "04-30") == (
        "Chaque année de N+1 à N+30 — mars à avril"
    )
    assert to_phrase(_paliers((3, None, 2, 3)), "07-01", "08-31") == (
        "Tous les 2 à 3 ans à partir de N+3 (2 ans retenu) — juillet à août"
    )


def test_phrase_explicite_alignee_sur_n_du_projet():
    fauches = {"type": "explicite", "ancrage_annee": 2026, "annees": list(range(2026, 2056))}
    assert to_phrase(fauches, annee_projet=2025) == "Chaque année de N+1 à N+30"
    seule = {"type": "explicite", "ancrage_annee": 2055, "annees": [2055]}
    assert to_phrase(seule, annee_projet=2025) == "En N+30 (2055)"


def test_phrase_sans_n_reste_en_annees_civiles():
    seule = {"type": "explicite", "ancrage_annee": 2055, "annees": [2055]}
    assert to_phrase(seule) == "En 2055 (année N du projet non renseignée)"
    assert "En N (2055)" not in to_phrase(seule)


def test_phrase_explicite_isole_et_mixte():
    seul = {"type": "explicite", "annee_N": 2025, "annees_explicites": [0]}
    assert to_phrase(seul) == "En N (2025)"
    mixte = {
        "type": "explicite",
        "annee_N": 2025,
        "annees_explicites": [0, 1, 2, 3, 4, 5, 10, 20, 30],
    }
    assert to_phrase(mixte) == (
        "Chaque année de N à N+5, puis en N+10, N+20, N+30"
    )


def test_phrase_continue_conditionnelle_et_imprecise():
    assert to_phrase({"type": "continue", "annee_N": 2025, "duree_ans": 30}) == (
        "En continu sur toute la durée du plan"
    )
    condition = {
        "type": "dependant_evenement",
        "fenetre_annees": [1, 3],
        "regle_source": "si pertes importantes",
    }
    assert to_phrase(condition) == "Si nécessaire, entre N+1 et N+3 : si pertes importantes"
    assert to_phrase({"type": "non_precisee", "regle_source": "selon les indicateurs"}) == (
        "Fréquence non précisée : selon les indicateurs"
    )


def test_phrase_fenetre_a_cheval_et_annees():
    regle = _paliers((0, 1, 1, None), duree=1)
    assert to_phrase(regle, "09-01", "02-15", traverse_nouvel_an=True) == (
        "Chaque année de N à N+1 — septembre à mi-février (à cheval sur deux années)"
    )
    assert to_phrase(regle, avec_annees=True) == "Chaque année de N à N+1 (2025–2026)"


def test_convertir_preserve_les_annees():
    brut = {
        "type": "explicite",
        "ancrage_annee": 2025,
        "annees": [2025, 2026, 2027, 2028, 2029, 2030, 2035, 2045, 2055],
        "regle_source": "comités",
    }
    converti = convertir(brut, 2025)
    assert converti["type"] == "paliers"
    assert expand(converti) == expand(brut)
    assert any(p["pas"] == 1 for p in converti["paliers"])
    assert 10 in converti["annees_explicites"] or any(
        p["debut"] == 10 for p in converti["paliers"]
    )


def test_convertir_periodique_et_idempotent():
    periodique = {
        "type": "periodique",
        "intervalle_ans": 1,
        "ancrage_annee": 2025,
        "annee_fin": 2028,
    }
    une_fois = convertir(periodique)
    assert expand(une_fois) == expand(periodique) == [2025, 2026, 2027, 2028]
    deux_fois = convertir(une_fois)
    assert expand(deux_fois) == expand(une_fois)
    assert deux_fois["paliers"] == une_fois["paliers"]


def test_convertir_ne_perd_aucune_annee_isolee():
    brut = {"type": "explicite", "annee_N": 2025, "annees": [2025, 2040]}
    converti = convertir(brut)
    assert expand(converti) == [2025, 2040]
    assert converti["type"] == "explicite"
    assert converti["annees_explicites"] == [0, 15]


def test_propriete_anciennes_formes():
    cas = [
        {"type": "ponctuel", "ancrage_annee": 2019},
        {"type": "periodique", "intervalle_ans": 3, "ancrage_annee": 2020, "annee_fin": 2032},
        {"type": "explicite", "ancrage_annee": 2025, "annees": [2025, 2026, 2028, 2031]},
        {
            "type": "paliers",
            "ancrage_annee": 2019,
            "annee_fin": 2038,
            "paliers": [
                {"intervalle_ans": 1, "nombre_occurrences": 4},
                {"intervalle_ans": 3, "nombre_occurrences": 5},
            ],
        },
        {"type": "campagnes", "ancrage_annee": 2024, "duree_ans": 3, "occurrences_par_an": 2},
        {"type": "dependant_evenement", "regle_source": "si besoin"},
    ]
    for regle in cas:
        obtenu = convertir(regle)
        assert expand(obtenu) == expand(regle)
