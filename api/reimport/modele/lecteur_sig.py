"""Lecture du dossier sig/ : shapefile, GeoPackage, GeoJSON. Projection obligatoire."""

from __future__ import annotations

import zipfile
from pathlib import Path

from api.ocr.domain.ug_ids import normalize_ug_id
from api.reimport.modele.constats import Constat

_ATOMES = {"Polygon": "surf", "MultiPolygon": "surf", "LineString": "lin", "MultiLineString": "lin", "Point": "pct", "MultiPoint": "pct"}


def lire_sig(
    racine: Path,
    codes_ug: set[str],
    champs: dict[str, str] | None,
    commune: str = "",
) -> tuple[dict, list[Constat], list[dict]]:
    dossier = racine / "sig"
    if not dossier.is_dir():
        return {}, [], []
    constats: list[Constat] = []
    couches = _ouvrir_couches(dossier, constats, commune)
    geometries: dict[str, dict] = {}
    apercu = []
    for couche in couches:
        if couche["nom"].lower().startswith("emprise"):
            if not couche["crs"]:
                constats.append(Constat("erreur", "SIG", None, couche["nom"], "Système de projection absent."))
                continue
            geometries["emprise"] = {"wkt": couche["features"][0]["wkt"], "kind": "surf", "source": couche["nom"]}
            continue
        champ = _champ(couche, champs or {}, constats)
        if champ is None:
            apercu.append(couche["apercu"])
            continue
        for feature in couche["features"]:
            code = normalize_ug_id(str(feature["attributs"].get(champ) or ""))
            if not code or code not in codes_ug:
                constats.append(Constat(
                    "avertissement", "SIG", None, couche["nom"],
                    f"Entité « {feature['attributs'].get(champ)} » absente de l'onglet UG : non importée.",
                ))
                continue
            kind = _ATOMES.get(feature["type"], "surf")
            if code in geometries:
                constats.append(Constat("info", "SIG", None, code, "Plusieurs entités pour le même code : elles sont réunies."))
                geometries[code]["wkt"] = _union(geometries[code]["wkt"], feature["wkt"])
            else:
                geometries[code] = {"wkt": feature["wkt"], "kind": kind, "source": couche["nom"]}
        apercu.append(couche["apercu"])
    return geometries, constats, apercu


def _champ(couche, choisis, constats) -> str | None:
    if "code_ug" in {c.lower() for c in couche["champs"]}:
        return next(c for c in couche["champs"] if c.lower() == "code_ug")
    if couche["nom"] in choisis:
        nom = choisis[couche["nom"]]
        if nom not in couche["champs"]:
            constats.append(Constat("erreur", "SIG", None, couche["nom"], f"Le champ {nom} n'existe pas dans cette couche."))
            return None
        return nom
    constats.append(Constat(
        "info", "SIG", None, couche["nom"],
        "Choisissez le champ qui porte le code UG. Aucun champ code_ug n'est présent.",
    ))
    return None


def coordonnees_hors_wgs84(minx: float, miny: float, maxx: float, maxy: float) -> bool:
    """Vrai si l'emprise ne peut pas être des degrés (Lambert-93 déposé sans CRS, typiquement)."""
    return abs(minx) > 180 or abs(maxx) > 180 or abs(miny) > 90 or abs(maxy) > 90


def loin_de_france(minx: float, miny: float, maxx: float, maxy: float) -> bool:
    """Emprise Lambert-93 hors de la métropole, donc loin de la commune indiquée."""
    return maxx < 100_000 or minx > 1_250_000 or maxy < 6_000_000 or miny > 7_150_000


def _ouvrir_couches(dossier: Path, constats: list[Constat], commune: str = "") -> list[dict]:
    try:
        import geopandas as gpd
    except ImportError:
        constats.append(Constat("erreur", "SIG", None, None, "La lecture SIG n'est pas disponible sur ce serveur."))
        return []
    chemins = _decompresser(dossier, constats)
    couches = []
    for chemin in chemins:
        try:
            cadre = gpd.read_file(chemin)
        except Exception as exc:
            constats.append(Constat("erreur", "SIG", None, chemin.name, f"Lecture impossible : {exc}"))
            continue
        if cadre.crs is None:
            if chemin.suffix.lower() == ".geojson":
                minx, miny, maxx, maxy = cadre.total_bounds
                if coordonnees_hors_wgs84(minx, miny, maxx, maxy):
                    constats.append(Constat(
                        "erreur", "SIG", None, chemin.name,
                        "Ces coordonnées ne sont pas en degrés : ce n'est pas du WGS84. "
                        "Déclarez le système de projection (Lambert-93, par exemple).",
                    ))
                    continue
                cadre = cadre.set_crs(4326)
                constats.append(Constat("info", "SIG", None, chemin.name, "GeoJSON sans CRS : lu en WGS84."))
            else:
                constats.append(Constat("erreur", "SIG", None, chemin.name, "Système de projection absent (.prj ou CRS)."))
                continue
        if cadre.crs.to_epsg() != 2154:
            cadre = cadre.to_crs(2154)
        minx, miny, maxx, maxy = cadre.total_bounds
        if loin_de_france(minx, miny, maxx, maxy):
            lieu = f" (commune indiquée : {commune})" if commune else ""
            constats.append(Constat(
                "avertissement", "SIG", None, chemin.name,
                f"Les géométries tombent hors de la France métropolitaine{lieu}. Vérifiez le système de projection.",
            ))
        features = []
        for _, row in cadre.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            if not geom.is_valid:
                geom = geom.buffer(0)
                constats.append(Constat("avertissement", "SIG", None, chemin.name, "Géométrie invalide, corrigée."))
            features.append({
                "wkt": geom.wkt,
                "type": geom.geom_type,
                "attributs": {k: row[k] for k in cadre.columns if k != cadre.geometry.name},
            })
        champs = [c for c in cadre.columns if c != cadre.geometry.name]
        echantillon = {}
        for champ in champs:
            echantillon[champ] = [str(v) for v in cadre[champ].head(5).tolist()]
        couches.append({
            "nom": chemin.stem,
            "crs": str(cadre.crs),
            "champs": champs,
            "features": features,
            "apercu": {"couche": chemin.stem, "champs": echantillon, "nb": len(features)},
        })
    return couches


def _decompresser(dossier: Path, constats) -> list[Path]:
    for archive in dossier.rglob("*.zip"):
        try:
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if info.filename.startswith("/") or ".." in Path(info.filename).parts:
                        constats.append(Constat("erreur", "SIG", None, archive.name, "Chemin invalide dans le ZIP."))
                        continue
                    zf.extract(info, archive.parent)
        except zipfile.BadZipFile:
            constats.append(Constat("erreur", "SIG", None, archive.name, "ZIP illisible."))
    return [p for p in dossier.rglob("*") if p.suffix.lower() in {".shp", ".gpkg", ".geojson"}]


def _union(a: str, b: str) -> str:
    from shapely import wkt
    from shapely.ops import unary_union
    return unary_union([wkt.loads(a), wkt.loads(b)]).wkt
