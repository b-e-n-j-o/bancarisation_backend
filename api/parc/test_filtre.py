"""Tests du helper filtre_organisation (habilitation parc)."""

from uuid import UUID

from api.parc.filtre import filtre_organisation

ORG = UUID("a1000000-0000-0000-0000-000000000001")


def test_filtre_controleur_voit_tout():
    clause, params = filtre_organisation("controleur", None)
    assert clause == "TRUE"
    assert params == []


def test_filtre_admin_voit_tout():
    clause, params = filtre_organisation("admin", ORG)
    assert clause == "TRUE"
    assert params == []


def test_filtre_operateur_scope_org():
    clause, params = filtre_organisation("operateur", ORG)
    assert clause == "organisation_id = %s"
    assert params == [str(ORG)]


def test_filtre_operateur_sans_org_bloque():
    clause, params = filtre_organisation("operateur", None)
    assert clause == "FALSE"
    assert params == []
