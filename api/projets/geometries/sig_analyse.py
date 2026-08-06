"""
Analyse SIG sans écriture bancarisation.* — session temporaire jusqu'à /confirmer.

Réponse immédiate géométrique (locale) ; catégories via convention de nommage
déterministe (Compensation_Fadet → compensation / Fadet).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from api.ocr.multidocs.utils.sig import (
    lire_couche,
    normaliser_sig,
)

CategorieErc = Literal[
    "compensation", "evitement", "reduction", "accompagnement", "autre"
]

MAX_APERCU_BYTES = 400_000
WORK_ROOT = Path(tempfile.gettempdir()) / "kerelia_sig_analyses"


class ColonneProfil(BaseModel):
    nom: str
    type: str
    distinctes: int
    exemples: list[str] = Field(default_factory=list)


class IdentiteCouche(BaseModel):
    source: str = "nom_fichier"
    colonne: Optional[str] = None
    exemples: list[str] = Field(default_factory=list)


class CoucheAnalyse(BaseModel):
    couche_id: str
    nom_source: str
    nb_entites: int
    type_geometrie: str
    epsg_source: Optional[int] = None
    surface_ha: Optional[float] = None
    categorie_proposee: CategorieErc = "autre"
    cible: Optional[str] = None
    identite: IdentiteCouche = Field(default_factory=IdentiteCouche)
    colonnes: list[ColonneProfil] = Field(default_factory=list)
    confiance: float = 0.5
    avertissements: list[str] = Field(default_factory=list)
    apercu: Optional[dict[str, Any]] = None
    inclure: bool = True


class AnalyseSig(BaseModel):
    analyse_id: str
    fichier: str
    epsg_commun: Optional[int] = None
    bbox_4326: Optional[list[float]] = None
    surface_totale_ha: Optional[float] = None
    couches: list[CoucheAnalyse] = Field(default_factory=list)
    avertissements_globaux: list[str] = Field(default_factory=list)
    fichiers_ignores: list[str] = Field(default_factory=list)


class CoucheConfirmation(BaseModel):
    couche_id: str
    categorie: CategorieErc
    cible: Optional[str] = None
    inclure: bool = True


class ConfirmerSigBody(BaseModel):
    analyse_id: str
    couches: list[CoucheConfirmation]


def _work_dir(projet_id: UUID, analyse_id: str) -> Path:
    d = WORK_ROOT / str(projet_id) / analyse_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def charger_analyse(projet_id: UUID, analyse_id: str) -> AnalyseSig:
    path = _work_dir(projet_id, analyse_id) / "analyse.json"
    if not path.exists():
        raise FileNotFoundError(f"Analyse {analyse_id} introuvable ou expirée.")
    return AnalyseSig.model_validate_json(path.read_text(encoding="utf-8"))


def _simplifier_fc(gdf, tolerance: float = 8.0) -> dict[str, Any]:
    """Aperçu 4326 simplifié pour MapLibre."""
    try:
        g4326 = gdf.to_crs(epsg=4326)
    except Exception:  # noqa: BLE001
        return {"type": "FeatureCollection", "features": []}
    try:
        # simplify en mètres approx via 3857 puis retour 4326
        g3857 = gdf.to_crs(epsg=3857)
        simplified = g3857.geometry.simplify(tolerance, preserve_topology=True)
        g4326 = g3857.set_geometry(simplified).to_crs(epsg=4326)
    except Exception:  # noqa: BLE001
        pass
    # drop Z/M
    try:
        g4326 = g4326.copy()
        g4326["geometry"] = g4326.geometry.force_2d()
    except Exception:  # noqa: BLE001
        pass
    return json.loads(g4326.to_json())


def _budget_apercus(couches: list[CoucheAnalyse]) -> None:
    """Garantit ≤ 400 ko pour l'ensemble des aperçus."""
    total = sum(len(json.dumps(c.apercu or {})) for c in couches)
    if total <= MAX_APERCU_BYTES:
        return
    # Dégrader : centroïdes seulement
    for c in couches:
        feats = (c.apercu or {}).get("features") or []
        cents = []
        for f in feats:
            geom = f.get("geometry") or {}
            # bbox-ish : on garde juste un Point approximatif
            coords = geom.get("coordinates")
            if not coords:
                continue
            try:
                flat = list(_flatten_coords(coords))
                if not flat:
                    continue
                xs = [p[0] for p in flat]
                ys = [p[1] for p in flat]
                cents.append({
                    "type": "Feature",
                    "properties": f.get("properties") or {},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2],
                    },
                })
            except Exception:  # noqa: BLE001
                continue
        c.apercu = {"type": "FeatureCollection", "features": cents}
        c.avertissements.append(
            "Aperçu allégé (centroïdes) : géométrie détaillée au-delà du budget 400 ko"
        )


def _flatten_coords(coords):
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
        yield coords[:2]
        return
    for c in coords:
        yield from _flatten_coords(c)


def analyser_fichier_sig(
    *,
    projet_id: UUID,
    file_name: str,
    content: bytes,
) -> AnalyseSig:
    if not content:
        raise ValueError("Fichier vide.")

    analyse_id = uuid.uuid4().hex
    work = _work_dir(projet_id, analyse_id)
    dest = work / file_name
    dest.write_bytes(content)

    geo_dir = work / "_geo"
    doc = normaliser_sig(dest, doc_id=f"sig-{analyse_id[:8]}", geo_dir=geo_dir)

    couches: list[CoucheAnalyse] = []
    epsgs: set[int] = set()
    bbox: Optional[list[float]] = None
    surface_tot = 0.0
    has_surface = False
    avertissements_globaux: list[str] = []

    for i, bloc in enumerate(doc.blocs):
        couche_id = f"c{i + 1}"
        nom = bloc.meta.get("nom_source") or bloc.titre or bloc.meta["couche"]
        cat = bloc.meta.get("categorie_proposee") or "autre"
        cible = bloc.meta.get("cible")

        colonnes_profil: list[ColonneProfil] = []
        apercu = None
        surface_ha = None
        try:
            gdf = lire_couche(bloc)
            for col in [c for c in gdf.columns if c != "geometry"][:30]:
                serie = gdf[col].dropna()
                colonnes_profil.append(
                    ColonneProfil(
                        nom=str(col),
                        type=str(serie.dtype),
                        distinctes=int(serie.nunique()),
                        exemples=[str(v)[:40] for v in serie.unique()[:6]],
                    )
                )
            epsg = bloc.meta.get("epsg")
            if epsg:
                epsgs.add(int(epsg))
                apercu = _simplifier_fc(gdf)
                try:
                    g4326 = gdf.to_crs(epsg=4326)
                    b = g4326.total_bounds  # minx, miny, maxx, maxy
                    if bbox is None:
                        bbox = [float(x) for x in b]
                    else:
                        bbox = [
                            min(bbox[0], float(b[0])),
                            min(bbox[1], float(b[1])),
                            max(bbox[2], float(b[2])),
                            max(bbox[3], float(b[3])),
                        ]
                except Exception:  # noqa: BLE001
                    pass
                try:
                    if bloc.meta.get("type_geometrie") in ("Polygon", "MultiPolygon"):
                        surface_ha = float(gdf.to_crs(epsg=2154).geometry.area.sum()) / 10_000
                        surface_tot += surface_ha
                        has_surface = True
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            bloc.meta.setdefault("avertissements", []).append(f"Lecture géométrie : {exc}")

        confiance = 0.86 if cat != "autre" else 0.4
        nb = int(bloc.meta.get("nb_features") or 0)
        couche = CoucheAnalyse(
            couche_id=couche_id,
            nom_source=nom,
            nb_entites=nb,
            type_geometrie=str(bloc.meta.get("type_geometrie") or "inconnu"),
            epsg_source=bloc.meta.get("epsg"),
            surface_ha=round(surface_ha, 2) if surface_ha is not None else None,
            categorie_proposee=cat,  # type: ignore[arg-type]
            cible=cible,
            identite=IdentiteCouche(source="nom_fichier"),
            colonnes=colonnes_profil,
            confiance=confiance,
            avertissements=list(bloc.meta.get("avertissements") or []),
            apercu=apercu,
            inclure=nb > 0,
        )
        # Méta sidecar pour confirmer
        couche_meta = {
            "locator": bloc.locator,
            "sidecar": bloc.meta.get("sidecar"),
            "couche": bloc.meta.get("couche"),
            "epsg": bloc.meta.get("epsg"),
        }
        (work / f"{couche_id}.meta.json").write_text(
            json.dumps(couche_meta, ensure_ascii=False), encoding="utf-8"
        )
        couches.append(couche)

    _budget_apercus(couches)

    if len(epsgs) > 1:
        avertissements_globaux.append(
            "CRS hétérogènes dans le dépôt — reprojection individuelle ; epsg_commun null"
        )

    analyse = AnalyseSig(
        analyse_id=analyse_id,
        fichier=file_name,
        epsg_commun=next(iter(epsgs)) if len(epsgs) == 1 else None,
        bbox_4326=bbox,
        surface_totale_ha=round(surface_tot, 2) if has_surface else None,
        couches=couches,
        avertissements_globaux=avertissements_globaux,
        fichiers_ignores=[],
    )
    (work / "analyse.json").write_text(
        analyse.model_dump_json(indent=2), encoding="utf-8"
    )
    # Copie sidecar path relative à work
    shutil.copy2(dest, work / "source.bin") if False else None  # noqa: already written as dest
    return analyse


def chemin_sidecar_couche(projet_id: UUID, analyse_id: str, couche_id: str) -> dict[str, Any]:
    meta_path = _work_dir(projet_id, analyse_id) / f"{couche_id}.meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Métadonnées couche {couche_id} introuvables.")
    return json.loads(meta_path.read_text(encoding="utf-8"))
