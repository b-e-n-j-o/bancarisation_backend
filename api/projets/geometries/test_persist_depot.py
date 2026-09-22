"""Règles de rattachement du dépôt SIG — sans I/O base."""
from __future__ import annotations

from api.ocr.ingest_erc.ingest_erc.modeles import (
    ReferentielVerrouille,
    UGVerrouillee,
    ZoneCandidate,
)
from api.projets.geometries.persist_depot import destination_entite, zone_pour_entite


def _ref(**kwargs) -> ReferentielVerrouille:
    return ReferentielVerrouille(
        ugs=kwargs.get("ugs", []),
        projet=kwargs.get("projet", {}),
        actions=[],
        decisions=kwargs.get("decisions", {}),
        provenance=kwargs.get("provenance", {}),
        empreinte="test",
        verrouille_le="2026-01-01T00:00:00+00:00",
    )


def test_zone_filtre_plus_specifique():
    couche = "Compensation_Fadet"
    zones = [
        ZoneCandidate(id=couche, couche=couche, filtre=None, nb_entites=4, surface_ha=1),
        ZoneCandidate(
            id=f"{couche}::UG=UG2",
            couche=couche,
            filtre={"UG": "UG2"},
            nb_entites=2,
            surface_ha=0.4,
        ),
    ]
    z = zone_pour_entite(couche, {"UG": "UG2", "SURF": 12}, zones)
    assert z is not None
    assert z.id == f"{couche}::UG=UG2"
    z2 = zone_pour_entite(couche, {"UG": "UG9"}, zones)
    assert z2 is not None
    assert z2.id == couche


def test_destination_ug_depuis_zone_sig():
    zone = ZoneCandidate(id="Compensation::UG=2", couche="Compensation", filtre={"UG": "2"},
                         nb_entites=1, surface_ha=0.2)
    ref = _ref(
        ugs=[UGVerrouillee(ug_code="UG2", libelle="Landes", zone_sig="Compensation::UG=2")],
        provenance={"ug.UG2.zone_sig": {"origine": "be", "confiance": "haute"}},
    )
    dest = destination_entite(zone, ref)
    assert dest.statut == "ug"
    assert dest.ug_id == "ug2"
    assert dest.libelle == "Landes"
    assert dest.confiance == "haute"
    assert "BE" in dest.motif or "confirmée" in dest.motif


def test_destination_contexte_et_ignoree():
    z_ctx = ZoneCandidate(id="Evitement_route", couche="Evitement_route",
                          filtre=None, nb_entites=3, surface_ha=1)
    z_ign = ZoneCandidate(id="Emprise_travaux", couche="Emprise_travaux",
                          filtre=None, nb_entites=1, surface_ha=2)
    ref = _ref(decisions={
        "couches_contexte": [{"zone": "Evitement_route", "motif": "mesure EV-3 non soumise"}],
        "couches_ignorees": ["Emprise_travaux"],
    })
    ctx = destination_entite(z_ctx, ref)
    assert ctx.statut == "contexte"
    assert ctx.ug_id is None
    assert "EV-3" in (ctx.motif or "")

    ign = destination_entite(z_ign, ref)
    assert ign.statut == "non_affectee"
    assert ign.ug_id is None
    assert "ignorée" in (ign.motif or "")


def test_destination_sans_zone():
    dest = destination_entite(None, _ref())
    assert dest.statut == "non_affectee"
    assert dest.ug_id is None
    assert dest.motif == "aucune destination"


def test_ignorer_n_est_pas_ecartee():
    z = ZoneCandidate(id="x", couche="x", filtre=None, nb_entites=1, surface_ha=0)
    dest = destination_entite(z, _ref(decisions={"couches_ignorees": ["x"]}))
    assert dest.statut == "non_affectee"
    assert dest.statut != "ecartee"
