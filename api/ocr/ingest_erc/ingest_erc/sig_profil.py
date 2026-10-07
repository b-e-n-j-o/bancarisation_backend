"""Étape 1a — profil SIG et zones candidates (ZIP, GPKG, GeoJSON)."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import shapefile
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from api.projets.geometries.sig_io import (
    appliquer_crs,
    extraire_zip,
    identifier_epsg,
    lire_gdf,
    sources_depuis_chemin,
)

# Conservé pour les appels historiques (persist / tests).
__all__ = ["code_ug", "extraire_zip", "profiler_sig"]

from .cadastre import cle_parcelle, parse_ref
from .inventaire import norm
from .modeles import ColonneProfil, CoucheProfil, ZoneCandidate

RE_UG = re.compile(r"^\s*u\.?\s*g\.?\s*[-_ ]?\s*(\d{1,3}[a-z]?)\s*$", re.I)
EPSG_CIBLE = 2154


def code_ug(val, regles=None) -> str | None:
    if regles is not None:
        u = regles.unites(val)
        return u[0] if len(u) == 1 and len(str(val).strip()) <= 20 else None
    m = RE_UG.match(str(val or ""))
    return f"UG{m.group(1).upper()}" if m else None


def _profil_colonne(nom: str, valeurs: list, regles=None) -> ColonneProfil:
    vals = [str(v).strip() for v in valeurs if v not in (None, "") and str(v).strip() not in ("", "-")]
    c = ColonneProfil(nom=nom, nb_valeurs=len(vals), exemples=sorted(set(vals))[:5])
    if not vals:
        return c
    part_ug = sum(bool(code_ug(v, regles)) for v in vals) / len(vals)
    if part_ug >= 0.8:
        c.interpretation = "ug_plan"
    elif re.fullmatch(r"(?i)u\.?g\.?|unit[eé].*gestion", nom.strip()):
        c.interpretation = "autre_nomenclature"
    elif re.fullmatch(r"(19|20)\d\d", nom.strip()):
        c.interpretation = "annees"
    elif re.search(r"(?i)surf", nom):
        c.interpretation = "surface"
    elif sum(bool(re.fullmatch(r"\d{5}[0-9A-Z]{0,3}[A-Z]{1,2}\d{1,4}[a-z]?(_\w+)?", v)) for v in vals) / len(vals) > 0.8:
        c.interpretation = "ref_cadastrale"
    return c


def _type_erc(nom_couche: str) -> str | None:
    n = norm(nom_couche)
    for motif, t in (("compens", "C"), ("evit", "E"), ("reduc", "R"), ("accompagn", "A")):
        if motif in n:
            return t
    return None


def _charger_shp(shp: Path) -> tuple[list[str], list[dict], list, str, str]:
    cpg = shp.with_suffix(".cpg")
    enc = cpg.read_text().strip() if cpg.exists() else "latin1"
    enc = {"utf_8": "utf-8", "UTF-8": "utf-8"}.get(enc, enc)
    r = shapefile.Reader(str(shp), encoding=enc, encodingErrors="replace")
    champs = [f[0] for f in r.fields[1:]]
    recs = [dict(zip(champs, sr.record)) for sr in r.iterShapeRecords()]
    shapes = [shape(s.__geo_interface__) for s in r.shapes()]
    try:
        gdf = lire_gdf(shp)
        epsg, motif = identifier_epsg(shp, gdf)
    except Exception:
        epsg, motif = None, "CRS non identifié"
    return champs, recs, shapes, motif, str(r.shapeTypeName)


def _charger_gdf(path: Path, layer: str | None) -> tuple[list[str], list[dict], list, str, int | None, str]:
    gdf = lire_gdf(path, layer)
    epsg, motif = identifier_epsg(path, gdf)
    if epsg:
        gdf = appliquer_crs(gdf, epsg)
    champs = [c for c in gdf.columns if c != "geometry"]
    recs, shapes = [], []
    for _, row in gdf.iterrows():
        recs.append({c: row[c] for c in champs})
        shapes.append(row.geometry)
    geom_type = ""
    if shapes and shapes[0] is not None:
        geom_type = getattr(shapes[0], "geom_type", "") or ""
    return champs, recs, shapes, motif, epsg, geom_type


def _zones_depuis(
    *,
    nom: str,
    fichier: str,
    champs: list[str],
    recs: list[dict],
    shapes: list,
    crs_nom: str,
    epsg: int,
    geom_type: str,
    regles,
    couches: list,
    zones: list,
    geoms: dict,
) -> None:
    av: list[str] = []
    reproj = False
    if epsg != EPSG_CIBLE:
        tr = Transformer.from_crs(epsg, EPSG_CIBLE, always_xy=True)
        shapes = [transform(tr.transform, g) if g is not None else g for g in shapes]
        reproj = True
        av.append(f"Reprojetée de EPSG:{epsg} vers EPSG:{EPSG_CIBLE}")
    cols = [_profil_colonne(c, [rec.get(c) for rec in recs], regles) for c in champs]
    surf = round(sum(getattr(g, "area", 0) or 0 for g in shapes if g is not None) / 1e4, 3)
    cp = CoucheProfil(
        nom=nom, fichier=fichier, geom_type=geom_type,
        nb_entites=len(recs), crs_source=crs_nom, epsg=epsg,
        reprojetee=reproj, surface_ha=surf,
        colonnes=cols, avertissements=av,
    )
    for c in cols:
        if c.interpretation == "autre_nomenclature":
            cp.avertissements.append(
                f"Colonne « {c.nom} » ressemble à une UG mais ses valeurs ({', '.join(c.exemples[:3])}) "
                "ne suivent pas la nomenclature du plan → ignorée")
    couches.append(cp)

    def refs(rec):
        for c in cols:
            if c.interpretation == "ref_cadastrale":
                p = parse_ref(str(rec.get(c.nom)))
                if p:
                    return p["cle"]
        if {"section", "numero"} <= set(champs):
            return cle_parcelle(rec["section"], rec["numero"], rec.get("subdiv", ""))
        return None

    col_ug = next((c.nom for c in cols if c.interpretation == "ug_plan"), None)
    groupes = defaultdict(list)
    for rec, g in zip(recs, shapes):
        if g is None:
            continue
        groupes[rec.get(col_ug) if col_ug else None].append((rec, g))
    for val, items in groupes.items():
        zid = f"{nom}::{col_ug}={val}" if col_ug else nom
        geom = unary_union([g for _, g in items])
        geoms[zid] = geom
        zones.append(ZoneCandidate(
            id=zid, couche=nom, filtre={col_ug: val} if col_ug else None,
            ug_attribut=code_ug(val, regles) if col_ug else None, nb_entites=len(items),
            surface_ha=round(getattr(geom, "area", 0) / 1e4, 3),
            refs_cadastrales=sorted({x for x in (refs(rec) for rec, _ in items) if x}),
            type_erc_nom=_type_erc(nom)))


def profiler_sig(chemin_zip: str, regles=None) -> tuple[list[CoucheProfil], list[ZoneCandidate], dict]:
    couches, zones, geoms = [], [], {}
    for path, layer, nom in sources_depuis_chemin(chemin_zip):
        try:
            if path.suffix.lower() == ".shp":
                champs, recs, shapes, crs_nom, geom_type = _charger_shp(path)
                try:
                    gdf = lire_gdf(path)
                    epsg, crs_nom = identifier_epsg(path, gdf)
                except Exception:
                    epsg = None
            else:
                champs, recs, shapes, crs_nom, epsg, geom_type = _charger_gdf(path, layer)
        except Exception:
            continue
        if epsg is None:
            continue
        _zones_depuis(
            nom=nom, fichier=path.name, champs=champs, recs=recs, shapes=shapes,
            crs_nom=crs_nom, epsg=epsg, geom_type=geom_type, regles=regles,
            couches=couches, zones=zones, geoms=geoms,
        )
    return couches, zones, geoms
