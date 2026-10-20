"""Projection pgcal → échéances / occurrences, sans appel API."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[5]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.ocr.ingest_erc.plan_de_gestion_to_calendrier.pgcal.schemas import (  # noqa: E402
    CalendarResult, Citation, Frame, Occurrence, Rule,
)
from api.ocr.ingest_erc.plan_de_gestion_to_calendrier.pont import (  # noqa: E402
    fusionner, plages_mois, projeter,
)


def _regle(**kw) -> Rule:
    base = dict(
        rule_id="ES2-01", action_code="ES 2", sub_action="fauche", kind="recurrente",
        ug_codes=["UG1"], frequency_text="chaque année", window_text="avril à juin",
        decision="integre", citations=[Citation(page=12, quote="fauche d'avril à juin")],
    )
    base.update(kw)
    return Rule(**base)


def _occ(**kw) -> Occurrence:
    base = dict(
        occ_id="OCC-0001", rule_id="ES2-01", action_code="ES2", sub_action="fauche",
        ug_codes=["UG1"], rel_year=1, year=2026, months=[4, 5, 6], decision="integre",
        confidence=0.91,
    )
    base.update(kw)
    return Occurrence(**base)


def _resultat(*occs, rules=None) -> CalendarResult:
    return CalendarResult(
        document="plan.pdf",
        settings={},
        frame=Frame(duration_years=30, actions=[]),
        rules=rules or [_regle()],
        occurrences=list(occs),
    )


def test_plages_mois():
    assert plages_mois([4, 5, 6]) == [(4, 6, False)]
    assert plages_mois([3, 4, 7, 8]) == [(3, 4, False), (7, 8, False)]
    assert plages_mois([11, 12, 1, 2]) == [(11, 2, True)]
    assert plages_mois([]) == [(None, None, False)]


def test_projection_fenetres_et_code():
    proj = projeter(_resultat(
        _occ(months=[3, 4, 7, 8]),
        rules=[_regle()],
    ))
    assert len(proj.echeances) == 2
    assert len(proj.occurrences) == 2
    assert {o["mois_debut"] for o in proj.occurrences} == {3, 7}
    assert {o["annee"] for o in proj.occurrences} == {2026}
    assert proj.occurrences[0]["code"] == "ES 2"
    assert proj.echeances[0]["code_operation"] == "ES2"
    assert proj.echeances[0]["source"]["page"] == 12


def test_activite_continue_sur_plusieurs_annees():
    proj = projeter(_resultat(_occ(
        occ_id="OCC-0002", year=2025, rel_year=0, span_end_rel=2, months=[],
        pass_label="activité continue",
    )))
    assert sorted(o["annee"] for o in proj.occurrences) == [2025, 2026, 2027]


def test_declencheur_sans_date():
    proj = projeter(_resultat(
        _occ(occ_id="OCC-0003", year=None, rel_year=None, months=[], decision="declencheur",
             hypothesis="si crue"),
        rules=[_regle(kind="conditionnelle", trigger="si crue")],
    ))
    assert proj.occurrences == []
    assert len(proj.non_placables) == 1
    assert proj.non_placables[0]["recurrence"]["type"] == "dependant_evenement"


def test_fusion_remplace_le_placeholder_et_laisse_le_tableur():
    placeholder = {
        "id": "ES2-vide",
        "code_operation": "ES2",
        "avertissements": ["aucune date trouvée dans les documents"],
    }
    proj = projeter(_resultat(_occ()))
    ech, occ, non, _avert, notes = fusionner([placeholder], [], [placeholder], [], [proj], set())
    assert "ES2-vide" not in {e["id"] for e in ech}
    assert occ and occ[0]["annee"] == 2026
    assert any("remplacée" in n for n in notes)

    ech2, occ2, _, _, notes2 = fusionner([placeholder], [], [], [], [proj], {"ES2"})
    assert ech2 == [placeholder]
    assert occ2 == []
    assert any("tableur" in n for n in notes2)
