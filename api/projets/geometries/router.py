"""Routes géométries projet : liste + ingestion shapefile ZIP + analyse SIG."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response

from .crud import (
    compter_projets_parc_par_departement,
    lister_geometries_parc,
    lister_geometries_ug,
    renommer_ug,
)
from .export_shp import exporter_shp_zip
from .ingestion import (
    GeometryIngestError,
    est_erreur_connexion,
    ingest_shapefile_zip,
    persister_ug,
)
from .sig_analyse import (
    AnalyseSig,
    ConfirmerSigBody,
    analyser_fichier_sig,
    charger_analyse,
    chemin_sidecar_couche,
)
from api.cadastre import enrichir_cadastre_projet
from pydantic import BaseModel, Field

router = APIRouter()


class RenommerUgBody(BaseModel):
    libelle: str = Field(min_length=1, max_length=200)


@router.get("/projets/{projet_id}/geometries")
def list_geometries_route(projet_id: UUID) -> dict[str, Any]:
    try:
        return lister_geometries_ug(projet_id)
    except GeometryIngestError as exc:
        detail = str(exc)
        code = status.HTTP_400_BAD_REQUEST
        if "connection" in detail.lower() or "postgres" in detail.lower():
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=code, detail=detail) from exc


def _shp_zip_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/projets/{projet_id}/ugs/{ug_id}/export-shp")
def export_shp_ug_route(
    projet_id: UUID,
    ug_id: str,
    geom_id: str | None = None,
) -> Response:
    """ZIP shapefile Lambert 93 de l'UG (ou d'une géométrie si geom_id)."""
    try:
        data, filename = exporter_shp_zip(
            projet_id,
            ug_id=ug_id,
            geom_id=geom_id,
        )
    except GeometryIngestError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "aucune" in detail.lower() or "introuvable" in detail.lower() else status.HTTP_400_BAD_REQUEST
        if "connection" in detail.lower() or "postgres" in detail.lower():
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=code, detail=detail) from exc
    return _shp_zip_response(data, filename)


@router.get("/projets/{projet_id}/emprise/export-shp")
def export_shp_emprise_route(projet_id: UUID) -> Response:
    """ZIP shapefile Lambert 93 de l'emprise projet."""
    try:
        data, filename = exporter_shp_zip(projet_id, emprise=True)
    except GeometryIngestError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "aucune" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=detail) from exc
    return _shp_zip_response(data, filename)


@router.patch("/projets/{projet_id}/ugs/{ug_id}")
def renommer_ug_route(projet_id: UUID, ug_id: str, body: RenommerUgBody) -> dict[str, Any]:
    """Renomme une UG (libellé sur toutes ses géométries)."""
    try:
        return renommer_ug(projet_id, ug_id, body.libelle)
    except GeometryIngestError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "introuvable" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=detail) from exc


@router.get("/geometries/parc")
def list_geometries_parc_route(departement: str | None = None) -> dict[str, Any]:
    try:
        return lister_geometries_parc(departement=departement)
    except GeometryIngestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/geometries/parc/departements")
def list_parc_departements_route() -> list[dict[str, Any]]:
    try:
        return compter_projets_parc_par_departement()
    except GeometryIngestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/projets/{projet_id}/geometries/ingest",
    status_code=status.HTTP_201_CREATED,
)
async def ingest_geometry_route(
    projet_id: UUID,
    file: UploadFile = File(...),
    ug_id: Optional[str] = Form(default=None),
    libelle: str = Form(default=""),
    description: str = Form(default=""),
    is_emprise: bool = Form(default=False),
) -> dict[str, Any]:
    try:
        content = await file.read()
        result = ingest_shapefile_zip(
            projet_id=projet_id,
            file_name=file.filename or "geometrie.zip",
            content=content,
            ug_id=ug_id,
            libelle=libelle,
            description=description,
            is_emprise=is_emprise,
        )
    except GeometryIngestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    cadastre_info: dict[str, Any] | None = None
    if result.couche != "emprise":
        try:
            # Bbox de toutes les UG du projet (repli buffer 200 m si > 5000)
            cadastre_info = enrichir_cadastre_projet(projet_id).to_dict()
        except Exception as exc:  # noqa: BLE001
            cadastre_info = {"avertissements": [str(exc)], "nb_parcelles": 0}

    return {
        "id": result.id,
        "table": result.table,
        "couche": result.couche,
        "ug_id": result.ug_id,
        "libelle": result.libelle,
        "geometry_type": result.geometry_type,
        "nb_features_source": result.nb_features_source,
        "srid_source": result.srid_source,
        "cadastre": cadastre_info,
    }


@router.post(
    "/projets/{projet_id}/sig/analyse",
    status_code=status.HTTP_200_OK,
    response_model=AnalyseSig,
)
async def analyser_sig_route(
    projet_id: UUID,
    file: UploadFile = File(...),
) -> AnalyseSig:
    """Analyse un dépôt SIG sans écrire dans bancarisation.*."""
    try:
        content = await file.read()
        return analyser_fichier_sig(
            projet_id=projet_id,
            file_name=file.filename or "sig.zip",
            content=content,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/projets/{projet_id}/sig/analyse/{analyse_id}", response_model=AnalyseSig)
def get_analyse_sig_route(projet_id: UUID, analyse_id: str) -> AnalyseSig:
    try:
        return charger_analyse(projet_id, analyse_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _cadastre_arriere_plan(projet_id: UUID) -> None:
    """Hors du spinner UI : l'IGN peut prendre des dizaines de secondes."""
    try:
        enrichir_cadastre_projet(projet_id)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  Cadastre (arrière-plan) projet {projet_id} : {exc}", flush=True)


@router.post(
    "/projets/{projet_id}/sig/confirmer",
    status_code=status.HTTP_201_CREATED,
)
def confirmer_sig_route(
    projet_id: UUID,
    body: ConfirmerSigBody,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Écrit les couches validées (une ligne PostGIS par géométrie, même ug_id)."""
    import geopandas as gpd

    try:
        analyse = charger_analyse(projet_id, body.analyse_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    par_id = {c.couche_id: c for c in analyse.couches}
    crees: list[dict[str, Any]] = []

    try:
        for conf in body.couches:
            if not conf.inclure:
                continue
            couche = par_id.get(conf.couche_id)
            if couche is None:
                raise GeometryIngestError(f"couche_id inconnu : {conf.couche_id}")
            meta = chemin_sidecar_couche(projet_id, body.analyse_id, conf.couche_id)
            gdf = gpd.read_file(meta["sidecar"], layer=meta["couche"])
            ids = persister_ug(
                projet_id=projet_id,
                gdf=gdf,
                nom_source=couche.nom_source,
                categorie_erc=conf.categorie,
                cible=conf.cible if conf.cible is not None else couche.cible,
                analyse_id=body.analyse_id,
                source_fichier=analyse.fichier,
                is_emprise=(
                    conf.categorie == "autre"
                    and "emprise" in couche.nom_source.lower()
                ),
            )
            crees.append({
                "couche_id": conf.couche_id,
                "nom_source": couche.nom_source,
                "ids": ids,
                "nb": len(ids),
            })
    except GeometryIngestError as exc:
        print(f"⚠️  SIG confirmer : {exc}", flush=True)
        code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if est_erreur_connexion(exc)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  SIG confirmer : {exc}", flush=True)
        code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if est_erreur_connexion(exc)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    if any(c.get("nb") for c in crees):
        background_tasks.add_task(_cadastre_arriere_plan, projet_id)

    return {
        "analyse_id": body.analyse_id,
        "couches_persistees": crees,
        "total_entites": sum(c["nb"] for c in crees),
        "cadastre": None,
    }
