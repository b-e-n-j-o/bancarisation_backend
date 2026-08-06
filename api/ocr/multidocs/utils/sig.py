"""
sig.py — SHP (zippé), GeoPackage, GeoJSON → blocs "fiche de couche".

Un fichier SIG n'est pas un document : c'est un RÉFÉRENTIEL. Il entre dans le
pipeline par la famille `contexte`, en priorité 0 — AVANT le plan de gestion.

Grain : UNE COUCHE PAR BLOC. Locator : `couche!Compensation_Fadet`.
Le LLM ne voit qu'une FICHE (profil attributaire). Les géométries vivent dans
un GeoPackage sidecar, relu après mappage de colonnes.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Optional

from .corpus import Bloc, DocumentNormalise, TypeBloc

MAX_ECHANTILLON = 6
MAX_COLONNES = 30

# Convention fréquente BE : Compensation_Fadet, Evitement_Boisement…
_RE_CONVENTION = re.compile(
    r"^(?P<cat>compensation|evitement|évitement|reduction|réduction|"
    r"accompagnement|emprise)[_\-\s]+(?P<cible>.+)$",
    re.IGNORECASE,
)

_CAT_ALIAS = {
    "evitement": "evitement",
    "évitement": "evitement",
    "reduction": "reduction",
    "réduction": "reduction",
    "compensation": "compensation",
    "accompagnement": "accompagnement",
    "emprise": "autre",
}


def decode_zip_filename(info: zipfile.ZipInfo) -> str:
    """Corrige CP437→CP1252 (ex. ChiroptŠres → Chiroptères)."""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for enc in ("cp1252", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def proposer_categorie_cible(nom: str) -> tuple[str, Optional[str]]:
    """Propose (categorie, cible) depuis le nom de couche/fichier.

    Ex. ``Compensation_Fadet`` → (``compensation``, ``Fadet``).
    Sinon → (``autre``, None).
    """
    stem = Path(nom).stem.replace(" ", "_")
    m = _RE_CONVENTION.match(stem)
    if not m:
        return "autre", None
    cat = _CAT_ALIAS.get(m.group("cat").lower(), "autre")
    cible = m.group("cible").replace("_", " ").strip() or None
    return cat, cible


def zip_contient_shapefile(chemin: Path) -> bool:
    try:
        with zipfile.ZipFile(chemin) as zf:
            return any(n.lower().endswith(".shp") for n in zf.namelist())
    except zipfile.BadZipFile:
        return False


def normaliser_sig(
    chemin: Path,
    doc_id: str,
    geo_dir: Optional[Path] = None,
    **_,
) -> DocumentNormalise:
    geo_dir = Path(geo_dir or (chemin.parent / "_geo"))
    geo_dir.mkdir(parents=True, exist_ok=True)
    sidecar = geo_dir / f"{doc_id}.gpkg"

    blocs: list[Bloc] = []
    for nom_couche, gdf in _lire_couches(chemin):
        if gdf.empty:
            continue
        # GPKG n'aime pas certains caractères dans les noms de couche
        couche_safe = re.sub(r"[^\w\-]+", "_", nom_couche, flags=re.UNICODE).strip("_") or "couche"
        gdf.to_file(sidecar, layer=couche_safe, driver="GPKG")

        cat, cible = proposer_categorie_cible(nom_couche)
        avertissements: list[str] = []
        epsg = _epsg(gdf)
        if epsg is None:
            avertissements.append("CRS sans code EPSG : positionnement carte impossible")

        blocs.append(
            Bloc(
                locator=f"couche!{couche_safe}",
                type=TypeBloc.meta,
                texte=_fiche_couche(nom_couche, gdf),
                titre=nom_couche,
                meta={
                    "couche": couche_safe,
                    "nom_source": nom_couche,
                    "nb_features": int(len(gdf)),
                    "crs": str(gdf.crs) if gdf.crs is not None else None,
                    "epsg": epsg,
                    "type_geometrie": _type_dominant(gdf),
                    "colonnes": [c for c in gdf.columns if c != "geometry"],
                    "sidecar": str(sidecar),
                    "categorie_proposee": cat,
                    "cible": cible,
                    "avertissements": avertissements,
                },
            )
        )

    return DocumentNormalise(
        doc_id=doc_id,
        nom_fichier=chemin.name,
        format="sig",
        sha256="",
        blocs=blocs,
        meta={"sidecar": str(sidecar), "source": chemin.suffix.lower()},
    )


def _lire_couches(chemin: Path):
    """Rend (nom_couche, GeoDataFrame). ZIP multi-shapefiles inclus.

    On extrait le ZIP sur disque plutôt que `zip://…` : les noms accentués
    (Chiroptères) et les .dbf CP1252 cassent sinon la chaîne VSI/GDAL en UTF-8.
    """
    import geopandas as gpd
    import tempfile

    ext = chemin.suffix.lower()

    if ext == ".zip":
        _verifier_zip(chemin)
        # Charger TOUT avant de supprimer le tmp (un yield laisserait le
        # TemporaryDirectory se fermer trop tôt côté appelant non exhaustif).
        charges: list[tuple[str, object]] = []
        with tempfile.TemporaryDirectory(prefix="sig_zip_") as tmp:
            extrait = Path(tmp)
            _extraire_zip_decode(chemin, extrait)
            shps = sorted(extrait.rglob("*.shp"))
            if not shps:
                raise ValueError("Aucun fichier .shp trouvé dans l'archive ZIP.")
            vus: set[str] = set()
            for shp in shps:
                stem = shp.stem
                if stem in vus:
                    stem = f"{stem}_{shp.parent.name}"
                vus.add(stem)
                charges.append((stem, _lire_shapefile(shp)))
        yield from charges
        return

    if ext in (".gpkg", ".geopackage"):
        import fiona

        for couche in fiona.listlayers(str(chemin)):
            yield couche, gpd.read_file(chemin, layer=couche)
        return

    if ext == ".shp":
        yield Path(chemin).stem, _lire_shapefile(chemin)
        return

    yield Path(chemin).stem, gpd.read_file(chemin)


def _extraire_zip_decode(chemin: Path, dest: Path) -> None:
    """Extrait le ZIP en renommant les entrées avec un décodage CP437→CP1252."""
    with zipfile.ZipFile(chemin) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = decode_zip_filename(info)
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Chemin ZIP invalide : {name}")
            cible = dest / name
            cible.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, cible.open("wb") as out:
                out.write(src.read())


def _encoding_dbf(shp: Path) -> str:
    """Lit le .cpg s'il existe ; sinon privilégie CP1252 (exports ArcGIS FR)."""
    cpg = shp.with_suffix(".cpg")
    if cpg.exists():
        brut = cpg.read_bytes().strip()
        declare = ""
        for enc in ("ascii", "utf-8", "cp1252", "latin-1"):
            try:
                declare = brut.decode(enc).strip().lower().replace("_", "-")
                break
            except UnicodeDecodeError:
                continue
        if declare in ("utf-8", "utf8"):
            # Souvent mensonger sur les exports BE FR → on le tente en 1er,
            # _lire_shapefile repliera sur cp1252.
            return "utf-8"
        if declare in ("ansi", "windows-1252", "cp1252", "iso-8859-1", "latin1", "latin-1"):
            return "cp1252"
        if declare:
            return declare
    return "cp1252"


def _lire_shapefile(shp: Path):
    """Lecture robuste : essaie l'encodage du .cpg puis CP1252 / latin-1."""
    import geopandas as gpd

    # Si .cpg dit utf-8, cp1252 reste prioritaire (exports ArcGIS FR).
    premier = _encoding_dbf(shp)
    candidats = [premier, "cp1252", "latin-1", "ISO-8859-1", "utf-8"]
    if premier == "utf-8":
        candidats = ["cp1252", "latin-1", "utf-8", "ISO-8859-1"]

    vus: set[str] = set()
    dernier: Exception | None = None
    for enc in candidats:
        if enc in vus:
            continue
        vus.add(enc)
        for kwargs in (
            {"encoding": enc},
            {"encoding": enc, "engine": "fiona"},
        ):
            try:
                return gpd.read_file(shp, **kwargs)
            except TypeError:
                # Ancienne version sans engine=
                try:
                    return gpd.read_file(shp, encoding=enc)
                except Exception as exc:  # noqa: BLE001
                    dernier = exc
            except Exception as exc:  # noqa: BLE001
                dernier = exc
                msg = str(exc).lower()
                if "codec" in msg or "decode" in msg or "utf" in msg or "encoding" in msg:
                    continue
                # Erreur non liée à l'encodage : on remonte
                if "engine" in str(exc).lower():
                    continue
                raise
    raise ValueError(
        f"Lecture impossible de {shp.name} "
        f"(encodage table attributaire). Dernière erreur : {dernier}"
    ) from dernier


def _verifier_zip(chemin: Path) -> None:
    with zipfile.ZipFile(chemin) as zf:
        for info in zf.infolist():
            name = decode_zip_filename(info)
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Chemin ZIP invalide : {name}")


def lire_couche(bloc: Bloc):
    """Relit les entités depuis le sidecar GeoPackage."""
    import geopandas as gpd

    return gpd.read_file(bloc.meta["sidecar"], layer=bloc.meta["couche"])


def _epsg(gdf) -> Optional[int]:
    try:
        return int(gdf.crs.to_epsg()) if gdf.crs is not None else None
    except Exception:  # noqa: BLE001
        return None


def _type_dominant(gdf) -> str:
    types = gdf.geometry.geom_type.dropna()
    return types.mode().iloc[0] if len(types) else "inconnu"


def _fiche_couche(nom: str, gdf) -> str:
    epsg = _epsg(gdf)
    entetes = [
        f"Couche : {nom}",
        f"Entités : {len(gdf)} · géométrie : {_type_dominant(gdf)}",
        f"CRS : {gdf.crs} (EPSG:{epsg})" if epsg else f"CRS : {gdf.crs} — ⚠ pas de code EPSG",
    ]
    surface = _surface_totale_ha(gdf)
    if surface is not None:
        entetes.append(f"Surface totale : {surface:.2f} ha")

    lignes = ["", "| colonne | type | valeurs distinctes | échantillon |",
              "| --- | --- | --- | --- |"]
    colonnes = [c for c in gdf.columns if c != "geometry"][:MAX_COLONNES]
    for col in colonnes:
        serie = gdf[col].dropna()
        distinctes = int(serie.nunique())
        echantillon = ", ".join(str(v)[:40] for v in serie.unique()[:MAX_ECHANTILLON])
        lignes.append(f"| {col} | {serie.dtype} | {distinctes} | {echantillon} |")
    return "\n".join(entetes + lignes)


def _surface_totale_ha(gdf) -> Optional[float]:
    try:
        if _type_dominant(gdf) not in ("Polygon", "MultiPolygon"):
            return None
        return float(gdf.to_crs(epsg=2154).geometry.area.sum()) / 10_000
    except Exception:  # noqa: BLE001
        return None
