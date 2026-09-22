"""Contrôles unitaires du gabarit GéoMCE (ZIP, DBF, CIBLE, ID)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from shapely.geometry import Polygon

from api.geomce.constantes import CIBLES_FERMEES, DBF_SCHEMA, DBF_WIDTHS
from api.geomce.generation import build_geodataframe, write_zip
from api.geomce.validation import controler, joindre_cibles


def _poly(x: float, y: float) -> Polygon:
    return Polygon([(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)])


def test_cible_trop_longue_est_bloquante():
    cibles = [
        "Espaces naturels, agricoles, forestiers, maritimes ou de loisirs",
        "Patrimoine culturel et archéologique",
        "Habitats naturels",
    ]
    joined = joindre_cibles(cibles)
    assert len(joined) > DBF_WIDTHS["CIBLE"]

    rapport = controler(
        projet={
            "departement": "33",
            "geomce_nom": "MC1",
            "geomce_categorie": "C1-1-a",
            "geomce_cible": cibles,
        },
        geoms=[{"type_geom": "polygon"}],
        categories_ok={"C1-1-a"},
        nb_parties=1,
    )
    codes = [b.code for b in rapport.bloquants]
    assert "E10" in codes
    assert rapport.peut_exporter is False
    # Pas de troncature : l'aperçu conserve la concaténation complète.
    assert rapport.attributs_apercu["CIBLE"] == joined


def test_cible_verbatim_vocabulaire():
    assert "Equilibres biologiques" in CIBLES_FERMEES
    assert "Cible a preciser" in CIBLES_FERMEES


def test_id_identique_en_mode_eclate():
    attrs = {
        "ID": "1",
        "NOM": "MC1",
        "CIBLE": "Eau",
        "DESCRIPTIO": "-",
        "DECISION": "-",
        "REFEI": "-",
        "CATEGORIE": "C1-1-a",
    }
    gdf = build_geodataframe([_poly(0, 0), _poly(10, 10)], attrs, strategie="eclate")
    assert len(gdf) == 2
    assert list(gdf["ID"]) == [1, 1]
    assert str(gdf["ID"].dtype).startswith("int")


def test_zip_a_plat_et_schema_dbf(tmp_path: Path):
    attrs = {
        "ID": "1",
        "NOM": "MC1",
        "CIBLE": "Eau",
        "DESCRIPTIO": "-",
        "DECISION": "-",
        "REFEI": "-",
        "CATEGORIE": "C1-1-a",
    }
    gdf = build_geodataframe([_poly(0, 0), _poly(10, 10)], attrs, strategie="eclate")
    zip_path = write_zip(gdf, srid=2154, basename="mesure_test", out_dir=tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert all("/" not in n and "\\" not in n for n in names)
    stems = {Path(n).suffix.lower() for n in names}
    assert {".shp", ".shx", ".dbf"}.issubset(stems)

    import shapefile

    r = shapefile.Reader(str(tmp_path / "mesure_test" / "mesure_test"))
    fields = {f[0]: f for f in r.fields if f[0] != "DeletionFlag"}
    for spec in DBF_SCHEMA:
        name = str(spec["name"])
        assert fields[name][1] == spec["type"]
        assert fields[name][2] == int(spec["width"])
    recs = r.records()
    assert len(recs) == 2
    assert recs[0][0] == recs[1][0] == 1
    r.close()


def test_avertissement_drom():
    rapport = controler(
        projet={
            "departement": "974",
            "geomce_nom": "MC1",
            "geomce_categorie": "C1-1-a",
            "geomce_cible": ["Eau"],
        },
        geoms=[{"type_geom": "polygon"}],
        categories_ok={"C1-1-a"},
        nb_parties=1,
    )
    assert any(w.code == "W10" for w in rapport.avertissements)


def test_multipart_signale_non_documente():
    rapport = controler(
        projet={
            "departement": "33",
            "geomce_nom": "MC1",
            "geomce_categorie": "C1-1-a",
            "geomce_cible": ["Eau"],
        },
        geoms=[{"type_geom": "polygon"}],
        categories_ok={"C1-1-a"},
        strategie_geom="multipart",
        nb_parties=1,
    )
    assert any(w.code == "W11" for w in rapport.avertissements)
