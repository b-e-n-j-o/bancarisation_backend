"""Aperçu DXF, persistance locale, calage similitude vers EPSG:2154."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel

from api.carto import persist as cao_persist
from api.carto.dxf_processor import ProcesseurDXF
from api.carto.dxf_raster import (
    DepotCao,
    appliquer_pdf_et_apercus,
    apparier,
    bbox_depuis_rasters,
    extraire_references,
    fusionner_bbox,
    fusionner_complements,
    medias_depuis_bytes,
    ouvrir_depot,
    resume_rasters,
    serialiser_refs,
)
from api.documents.crud_document import DocumentServiceError, upload_document

logger = logging.getLogger(__name__)

router = APIRouter()

TAILLE_MAX_OCTETS = 120 * 1024 * 1024
FLECHE_PREVIEW_M = 0.05


class GroupesBody(BaseModel):
    groupes: dict[str, Any]
    visibles: list[str] | None = None


class PaireCalage(BaseModel):
    src: list[float]
    dst_2154: list[float]


class CalageBody(BaseModel):
    paires: list[PaireCalage]
    mode: str = "deux_points"


class BboxPciBody(BaseModel):
    west: float
    south: float
    east: float
    north: float


def _lire_depot(nom: str, contenu: bytes) -> DepotCao:
    if not contenu:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Fichier vide.")
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Fichier trop volumineux (max 120 Mo).",
        )
    try:
        return ouvrir_depot(nom, contenu)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _extraire(
    depot: DepotCao,
    inclure_calque_0: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int], list[dict[str, Any]], dict[str, Any]]:
    ignores = [] if inclure_calque_0 else ["0"]
    tmp_path = None
    proc: ProcesseurDXF | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp.write(depot.dxf_bytes)
            tmp_path = tmp.name
        proc = ProcesseurDXF(
            tmp_path,
            calques_ignores=ignores,
            inclure_textes=True,
            fleche_metres=FLECHE_PREVIEW_M,
            aplatir_z=True,
            arrondi=3,
        )
        if not proc.charger():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="DXF illisible (format corrompu ou non supporté).",
            )
        collection = proc.traiter()
        refs, non_placees = extraire_references(proc.doc, proc.facteur)
        apparier(refs, depot.fichiers)
        appliquer_pdf_et_apercus(refs, depot.fichiers, proc.facteur)
        unused = [c for c in depot.fichiers if c not in {r.get("chemin_archive") for r in refs}]
        rasters = serialiser_refs(refs)
        resume = resume_rasters(refs, non_placees, unused)
        meta = collection.get("metadata") or {}
        meta["bbox_locale"] = fusionner_bbox(meta.get("bbox_locale"), bbox_depuis_rasters(refs))
        collection["metadata"] = meta
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Échec extraction DXF")
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Impossible d'extraire le DXF : {exc}",
        ) from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    assert proc is not None
    calques = proc.resume_calques_detail()
    par = {c["nom"]: c for c in calques}
    for r in rasters:
        nom = r.get("calque") or "0"
        c = par.get(nom)
        if not c:
            c = {"nom": nom, "nb": 0, "types": {}, "visible_defaut": nom != "0"}
            par[nom] = c
            calques.append(c)
        t = r.get("dxf_type") or "IMAGE"
        c["types"][t] = c["types"].get(t, 0) + 1
        c["nb"] = int(c.get("nb") or 0) + 1
    return collection, calques, dict(proc.calques_masques), rasters, resume


def _payload_preview(
    projet_id: UUID,
    nom: str,
    collection: dict[str, Any],
    calques: list[dict[str, Any]],
    masques: dict[str, int],
    inclure_calque_0: bool,
    rasters: list[dict[str, Any]],
    rasters_resume: dict[str, Any],
    *,
    plan_id: str | None = None,
    groupes: dict[str, Any] | None = None,
    visibles: list[str] | None = None,
) -> dict[str, Any]:
    meta = collection.get("metadata") or {}
    bbox = meta.get("bbox_locale")
    features = collection.get("features") or []
    body: dict[str, Any] = {
        "projet_id": str(projet_id),
        "nom_fichier": nom,
        "nb_entites": len(features),
        "calque_0_inclus": inclure_calque_0,
        "metadata": {
            "dxf_version": meta.get("dxf_version"),
            "insunits": meta.get("insunits"),
            "facteur_applique": meta.get("facteur_applique"),
            "unite_sortie": meta.get("unite_sortie"),
            "entites_non_converties": meta.get("entites_non_converties") or {},
            "bbox_locale": bbox,
            "largeur": (bbox["xmax"] - bbox["xmin"]) if bbox else None,
            "hauteur": (bbox["ymax"] - bbox["ymin"]) if bbox else None,
        },
        "calques": calques,
        "calques_masques": masques,
        "geojson": {"type": "FeatureCollection", "features": features},
        "rasters": rasters,
        "rasters_resume": rasters_resume,
    }
    if plan_id:
        body["plan_id"] = plan_id
    if groupes:
        body["groupes"] = groupes
    if visibles is not None:
        body["visibles"] = visibles
    return body


def _http_persist(exc: cao_persist.PlanCaoError) -> HTTPException:
    msg = str(exc)
    code = status.HTTP_404_NOT_FOUND if "introuvable" in msg.lower() else status.HTTP_400_BAD_REQUEST
    if "injoignable" in msg.lower():
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(status_code=code, detail=msg)


async def _depot_requete(
    fichier: UploadFile,
    complements: UploadFile | None,
) -> DepotCao:
    nom = fichier.filename or "plan.dxf"
    data = await fichier.read()
    depot = _lire_depot(nom, data)
    if complements is not None and complements.filename:
        cdata = await complements.read()
        if cdata:
            try:
                depot = fusionner_complements(depot, complements.filename, cdata)
            except ValueError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return depot


@router.post("/projets/{projet_id}/cao/preview")
async def preview_dxf(
    projet_id: UUID,
    fichier: UploadFile = File(...),
    complements: UploadFile | None = File(default=None),
    inclure_calque_0: bool = Query(default=False),
) -> dict[str, Any]:
    depot = await _depot_requete(fichier, complements)
    collection, calques, masques, rasters, resume = _extraire(depot, inclure_calque_0)
    return _payload_preview(
        projet_id, depot.dxf_nom, collection, calques, masques, inclure_calque_0, rasters, resume,
    )


@router.post("/projets/{projet_id}/cao/plans", status_code=status.HTTP_201_CREATED)
async def creer_plan(
    projet_id: UUID,
    fichier: UploadFile = File(...),
    complements: UploadFile | None = File(default=None),
    inclure_calque_0: bool = Query(default=False),
) -> dict[str, Any]:
    depot = await _depot_requete(fichier, complements)
    collection, calques, masques, rasters, resume = _extraire(depot, inclure_calque_0)

    document_id = None
    try:
        doc = upload_document(
            projet_id,
            depot.dxf_nom,
            depot.dxf_bytes,
            "application/dxf",
            "cao",
            None,
            "Plan CAO (DXF)",
            sous_dossier="cao",
        )
        if doc.get("id"):
            document_id = UUID(str(doc["id"]))
    except DocumentServiceError as exc:
        logger.warning("DXF non déposé dans le bucket (%s) — persistance géom. quand même", exc)

    try:
        plan_id = cao_persist.persister(
            projet_id,
            nom_fichier=depot.dxf_nom,
            document_id=document_id,
            collection=collection,
            calques=calques,
            calques_masques=masques,
            calque_0_inclus=inclure_calque_0,
            rasters=rasters,
            rasters_resume=resume,
        )
        enregistre = cao_persist.charger(projet_id, UUID(plan_id))
    except cao_persist.PlanCaoError as exc:
        raise _http_persist(exc) from exc
    return enregistre


@router.post("/projets/{projet_id}/cao/plans/{plan_id}/archive")
async def joindre_archive(
    projet_id: UUID,
    plan_id: UUID,
    fichier: UploadFile = File(...),
) -> dict[str, Any]:
    """Relie images/PDF d'un ZIP (ou d'un fichier isolé) aux références du plan déjà chargé."""
    nom = fichier.filename or "complements.zip"
    data = await fichier.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Fichier vide.")
    if len(data) > TAILLE_MAX_OCTETS:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Fichier trop volumineux (max 120 Mo).",
        )
    try:
        medias = medias_depuis_bytes(nom, data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not medias:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Aucun image/PDF dans ce fichier. Nommez-les comme dans le DXF (ex. IMG_4330.JPG).",
        )

    try:
        actuel = cao_persist.charger(projet_id, plan_id)
    except cao_persist.PlanCaoError as exc:
        raise _http_persist(exc) from exc

    refs = list(actuel.get("rasters") or [])
    ok = [r for r in refs if r.get("statut") == "apparie"]
    manque = [r for r in refs if r.get("statut") != "apparie"]
    apparier(manque, medias)
    facteur = float((actuel.get("metadata") or {}).get("facteur_applique") or 1) or 1.0
    appliquer_pdf_et_apercus(manque, medias, facteur)
    fusion = serialiser_refs(ok + manque)
    unused = [c for c in medias if c not in {r.get("chemin_archive") for r in fusion}]
    non_placees = (actuel.get("rasters_resume") or {}).get("definitions_non_placees") or []
    resume = resume_rasters(ok + manque, non_placees, unused)
    try:
        cao_persist.enregistrer_rasters(projet_id, plan_id, fusion, resume)
        return cao_persist.charger(projet_id, plan_id)
    except cao_persist.PlanCaoError as exc:
        raise _http_persist(exc) from exc


@router.get("/projets/{projet_id}/cao/plans")
def lister_plans(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return cao_persist.lister(projet_id)
    except cao_persist.PlanCaoError as exc:
        raise _http_persist(exc) from exc


@router.get("/projets/{projet_id}/cao/plans/{plan_id}")
def charger_plan(projet_id: UUID, plan_id: UUID) -> dict[str, Any]:
    try:
        return cao_persist.charger(projet_id, plan_id)
    except cao_persist.PlanCaoError as exc:
        raise _http_persist(exc) from exc


@router.put("/projets/{projet_id}/cao/plans/{plan_id}/groupes")
def sauver_groupes(projet_id: UUID, plan_id: UUID, body: GroupesBody) -> dict[str, bool]:
    try:
        cao_persist.enregistrer_groupes(
            projet_id,
            plan_id,
            body.groupes,
            body.visibles,
        )
    except cao_persist.PlanCaoError as exc:
        raise _http_persist(exc) from exc
    return {"ok": True}


def _shp_zip_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/projets/{projet_id}/cao/plans/{plan_id}/export-shp")
def export_shp_plan(projet_id: UUID, plan_id: UUID) -> Response:
    """ZIP shapefile Lambert-93 des entités déjà calées (`geom`)."""
    from api.carto.export_shp import exporter_shp_zip

    try:
        data, filename = exporter_shp_zip(projet_id, plan_id)
    except cao_persist.PlanCaoError as exc:
        raise _http_persist(exc) from exc
    return _shp_zip_response(data, filename)


@router.put("/projets/{projet_id}/cao/plans/{plan_id}/calage")
def caler_plan(projet_id: UUID, plan_id: UUID, body: CalageBody) -> dict[str, Any]:
    from api.carto.calage import CalageError, paires_depuis_body, similitude_deux_points

    raw = [p.model_dump() for p in body.paires]
    try:
        s1, d1, s2, d2 = paires_depuis_body(raw)
        params = similitude_deux_points(s1, d1, s2, d2)
        enregistre = cao_persist.appliquer_calage(
            projet_id,
            plan_id,
            mode=body.mode if body.mode in ("deux_points", "manuel") else "deux_points",
            tx=params["tx"],
            ty=params["ty"],
            rotation_rad=params["rotation_rad"],
            echelle=params["echelle"],
            paires=raw[:2],
        )
    except CalageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except cao_persist.PlanCaoError as exc:
        raise _http_persist(exc) from exc
    return enregistre


@router.post("/projets/{projet_id}/cao/pci-bbox")
def pci_bbox(projet_id: UUID, body: BboxPciBody) -> dict[str, Any]:
    """Cadastre PCI WFS pour le calage — session uniquement, non persisté."""
    from api.carto.pci_wfs import PciWfsError, parcelles_pour_bbox

    try:
        with cao_persist._connect() as conn:
            cao_persist._verifier_projet(conn, projet_id)
    except cao_persist.PlanCaoError as exc:
        raise _http_persist(exc) from exc
    try:
        return parcelles_pour_bbox(body.west, body.south, body.east, body.north)
    except PciWfsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
