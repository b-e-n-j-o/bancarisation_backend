"""Routes API cadastre (snapshot autour des UG)."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response

from .crud import CadastreReadError, lister_cadastre_projet, lister_parcelles_pour_ug
from .enrichissement import enrichir_cadastre_projet
from .export import exporter_parcellaire_intersections, feature_collection_to_csv
from .ign_client import DEFAULT_BUFFER_M, CadastreIgnError

router = APIRouter()


@router.get("/projets/{projet_id}/cadastre")
def get_cadastre_route(
    projet_id: UUID,
    ug_id: Optional[str] = Query(default=None),
    croisement: bool = Query(
        default=False,
        description="Uniquement les parcelles qui intersectent une UG (onglet Foncier).",
    ),
) -> dict[str, Any]:
    """GeoJSON des parcelles cadastrales stockées pour le projet (ou une UG)."""
    try:
        return lister_cadastre_projet(projet_id, ug_id=ug_id, croisement=croisement)
    except CadastreReadError as exc:
        detail = str(exc)
        code = status.HTTP_400_BAD_REQUEST
        if "does not exist" in detail.lower() or "undefinedtable" in detail.lower().replace(" ", ""):
            return {
                "type": "FeatureCollection",
                "features": [],
                "meta": {"nb_parcelles": 0, "ug_id": ug_id, "avertissement": detail},
            }
        if "connection" in detail.lower() or "postgres" in detail.lower():
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=code, detail=detail) from exc


@router.get("/projets/{projet_id}/ugs/{ug_id}/parcelles")
def get_parcelles_ug_route(projet_id: UUID, ug_id: str) -> dict[str, Any]:
    """Parcelles cadastrales qui composent une UG (croisement spatial)."""
    try:
        rows = lister_parcelles_pour_ug(projet_id, ug_id)
        return {"ug_id": ug_id, "nb_parcelles": len(rows), "parcelles": rows}
    except CadastreReadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/projets/{projet_id}/cadastre/export-parcellaire")
def export_parcellaire_route(
    projet_id: UUID,
    ug_id: Optional[list[str]] = Query(
        default=None,
        description="Une ou plusieurs UG ; absent = toutes les UG du projet",
    ),
    format: str = Query(default="geojson", pattern="^(geojson|csv)$"),
) -> Any:
    """Export des intersections UG ∩ parcelles (géométries tronquées).

    - ``geojson`` : FeatureCollection (morceaux)
    - ``csv`` : une ligne / intersection + ``wkt_4326`` + ``tronquee``
    """
    try:
        ug_ids = [u for u in (ug_id or []) if u and u.strip()] or None
        fc = exporter_parcellaire_intersections(projet_id, ug_ids=ug_ids)
    except CadastreReadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if format == "csv":
        body = feature_collection_to_csv(fc)
        filename = f"parcellaire_{projet_id}.csv"
        return Response(
            content=body.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return fc


@router.post(
    "/projets/{projet_id}/cadastre/rafraichir",
    status_code=status.HTTP_200_OK,
)
def rafraichir_cadastre_route(
    projet_id: UUID,
    buffer_m: float = Query(default=DEFAULT_BUFFER_M, ge=10, le=2000),
    ug_id: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Recalcule le snapshot cadastre (toutes les UG ou une seule)."""
    try:
        result = enrichir_cadastre_projet(
            projet_id,
            buffer_m=buffer_m,
            ug_ids=[ug_id] if ug_id else None,
        )
        return result.to_dict()
    except CadastreIgnError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Enrichissement cadastre impossible: {exc}",
        ) from exc
