"""Étape 1a — profil SIG et zones candidates.

Lecture d'une archive (shapefiles) ; en prod on passera par geopandas/pyogrio
qui lit aussi le GPKG. Ici pyshp + shapely + pyproj pour rester léger.
"""
from __future__ import annotations

import re
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from .cadastre import cle_parcelle, parse_ref
from .inventaire import norm
from .modeles import ColonneProfil, CoucheProfil, ZoneCandidate

RE_UG = re.compile(r"^\s*u\.?\s*g\.?\s*[-_ ]?\s*(\d{1,3}[a-z]?)\s*$", re.I)
EPSG_CIBLE = 2154


def code_ug(val) -> str | None:
    m = RE_UG.match(str(val or ""))
    return f"UG{m.group(1).upper()}" if m else None


def _nom_zip(info: zipfile.ZipInfo) -> str:
    """Répare les noms de fichiers abîmés par les allers-retours Windows/Mac.

    1. sans flag UTF-8, zipfile décode en cp437 : on retente en UTF-8 ;
    2. normalisation NFC (macOS écrit en NFD : 'S' + caron) ;
    3. caractères de la plage cp1252 0x80-0x9F (ex. 'Š') = accents cp850 mal lus
       ('Š' = 0x8A = 'è' en cp850) : on les remappe un par un.
    """
    nom = info.filename
    if not info.flag_bits & 0x800:
        try:
            nom = nom.encode("cp437").decode("utf-8")
        except UnicodeError:
            pass
    nom = unicodedata.normalize("NFC", nom)
    out = []
    for ch in nom:
        try:
            b = ch.encode("cp1252")
            out.append(b.decode("cp850") if 0x80 <= b[0] <= 0x9F else ch)
        except UnicodeError:
            out.append(ch)
    return "".join(out)


def extraire_zip(chemin: str) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="sig_"))
    with zipfile.ZipFile(chemin) as z:
        for info in z.infolist():
            nom = _nom_zip(info)
            if info.is_dir() or "__MACOSX" in nom or Path(nom).name.startswith("._"):
                continue
            cible = dest / Path(nom).name
            cible.write_bytes(z.read(info))
    return dest


def _crs(shp: Path) -> tuple[str, int | None]:
    prj = shp.with_suffix(".prj")
    if not prj.exists():
        return "absent", None
    wkt = prj.read_text(errors="ignore")
    try:
        crs = CRS.from_wkt(wkt)
        return crs.name, crs.to_epsg(min_confidence=25)
    except Exception:
        return wkt[:40], None


def _profil_colonne(nom: str, valeurs: list) -> ColonneProfil:
    vals = [str(v).strip() for v in valeurs if v not in (None, "") and str(v).strip() not in ("", "-")]
    c = ColonneProfil(nom=nom, nb_valeurs=len(vals), exemples=sorted(set(vals))[:5])
    if not vals:
        return c
    part_ug = sum(bool(code_ug(v)) for v in vals) / len(vals)
    if part_ug >= 0.8:
        c.interpretation = "ug_plan"
    elif re.fullmatch(r"(?i)u\.?g\.?|unit[eé].*gestion", nom.strip()):
        c.interpretation = "autre_nomenclature"   # ex. UG forestière "09.04b"
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


def profiler_sig(chemin_zip: str) -> tuple[list[CoucheProfil], list[ZoneCandidate], dict]:
    dossier = extraire_zip(chemin_zip)
    couches, zones, geoms = [], [], {}
    for shp in sorted(dossier.glob("*.shp")):
        cpg = shp.with_suffix(".cpg")
        enc = cpg.read_text().strip() if cpg.exists() else "latin1"
        enc = {"utf_8": "utf-8", "UTF-8": "utf-8"}.get(enc, enc)
        r = shapefile.Reader(str(shp), encoding=enc, encodingErrors="replace")
        champs = [f[0] for f in r.fields[1:]]
        recs = [dict(zip(champs, sr.record)) for sr in r.iterShapeRecords()]
        shapes = [shape(s.__geo_interface__) for s in r.shapes()]
        crs_nom, epsg = _crs(shp)
        av = []
        reproj = False
        if epsg is None:
            av.append("CRS non identifié : couche ignorée (refus v0)")
            continue
        if epsg != EPSG_CIBLE:
            tr = Transformer.from_crs(epsg, EPSG_CIBLE, always_xy=True)
            shapes = [transform(tr.transform, g) for g in shapes]
            reproj = True
            av.append(f"Reprojetée de EPSG:{epsg} vers EPSG:{EPSG_CIBLE}")
        cols = [_profil_colonne(c, [rec[c] for rec in recs]) for c in champs]
        cp = CoucheProfil(nom=shp.stem, fichier=shp.name, geom_type=r.shapeTypeName,
                          nb_entites=len(recs), crs_source=crs_nom, epsg=epsg,
                          reprojetee=reproj, surface_ha=round(sum(g.area for g in shapes) / 1e4, 3),
                          colonnes=cols, avertissements=av)
        for c in cols:
            if c.interpretation == "autre_nomenclature":
                cp.avertissements.append(
                    f"Colonne « {c.nom} » ressemble à une UG mais ses valeurs ({', '.join(c.exemples[:3])}) "
                    "ne suivent pas la nomenclature du plan → ignorée")
        couches.append(cp)

        # références cadastrales par entité
        def refs(rec):
            for c in cols:
                if c.interpretation == "ref_cadastrale":
                    p = parse_ref(str(rec[c.nom]))
                    if p:
                        return p["cle"]
            if {"section", "numero"} <= set(champs):
                return cle_parcelle(rec["section"], rec["numero"], rec.get("subdiv", ""))
            return None

        col_ug = next((c.nom for c in cols if c.interpretation == "ug_plan"), None)
        groupes = defaultdict(list)
        for rec, g in zip(recs, shapes):
            groupes[rec[col_ug] if col_ug else None].append((rec, g))
        for val, items in groupes.items():
            zid = f"{shp.stem}::{col_ug}={val}" if col_ug else shp.stem
            geom = unary_union([g for _, g in items])
            geoms[zid] = geom
            zones.append(ZoneCandidate(
                id=zid, couche=shp.stem, filtre={col_ug: val} if col_ug else None,
                ug_attribut=code_ug(val) if col_ug else None, nb_entites=len(items),
                surface_ha=round(geom.area / 1e4, 3),
                refs_cadastrales=sorted({x for x in (refs(rec) for rec, _ in items) if x}),
                type_erc_nom=_type_erc(shp.stem)))
    return couches, zones, geoms
