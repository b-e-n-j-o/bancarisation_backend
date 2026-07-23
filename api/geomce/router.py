"""API Versement GéoMCE — contrôle, aperçu, export, historique."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.geomce import historique as hist
from api.geomce.constantes import CIBLES_FERMEES, STRATEGIE_GEOM_DEFAUT
from api.geomce.generation import (
    build_geodataframe,
    make_basename,
    prepare_export_payload,
    write_zip,
)
from api.geomce.validation import controler

router = APIRouter(prefix="/projets/{projet_id}/geomce", tags=["geomce"])


class GeomceChampsPatch(BaseModel):
    geomce_nom: Optional[str] = Field(default=None, max_length=50)
    geomce_categorie: Optional[str] = None
    geomce_cible: Optional[list[str]] = None
    geomce_description: Optional[str] = Field(default=None, max_length=254)
    geomce_projet_libelle: Optional[str] = None
    geomce_procedure_libelle: Optional[str] = None
    reference_decision: Optional[str] = None
    reference_ei: Optional[str] = None


class ExportRequest(BaseModel):
    mode: Literal["complet", "geometrie_seule"] = "complet"
    strategie_geom: Literal["multipart", "eclate"] = STRATEGIE_GEOM_DEFAUT  # type: ignore[assignment]
    confirmer_doublon: bool = False


class ExportPatch(BaseModel):
    statut: Optional[Literal["genere", "verse", "abandonne"]] = None
    verse_le: Optional[date] = None
    commentaire: Optional[str] = None


def _projet_or_404(projet_id: UUID) -> dict[str, Any]:
    p = hist.lire_projet(projet_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Projet introuvable")
    return p


@router.get("/cibles")
def list_cibles(projet_id: UUID) -> list[str]:
    return list(CIBLES_FERMEES)


@router.get("/categories")
def list_categories(projet_id: UUID) -> list[dict[str, Any]]:
    try:
        return hist.lister_categories()
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/fiche")
def get_fiche(projet_id: UUID) -> dict[str, Any]:
    p = _projet_or_404(projet_id)
    return {
        "projet_id": str(p["id"]),
        "nom": p.get("nom"),
        "departement": p.get("departement"),
        "geomce_nom": p.get("geomce_nom"),
        "geomce_nom_verrouille": bool(p.get("geomce_nom_verrouille")),
        "geomce_categorie": p.get("geomce_categorie"),
        "geomce_cible": p.get("geomce_cible") or [],
        "geomce_description": p.get("geomce_description"),
        "geomce_projet_libelle": p.get("geomce_projet_libelle"),
        "geomce_procedure_libelle": p.get("geomce_procedure_libelle"),
        "reference_decision": p.get("reference_decision"),
        "reference_ei": p.get("reference_ei"),
        "description": p.get("description"),
    }


@router.patch("/fiche")
def patch_fiche(projet_id: UUID, body: GeomceChampsPatch) -> dict[str, Any]:
    _projet_or_404(projet_id)
    try:
        p = hist.mettre_a_jour_champs_geomce(
            projet_id,
            body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return get_fiche(projet_id) if p else get_fiche(projet_id)


@router.get("/controle")
def controle(
    projet_id: UUID,
    mode: Literal["complet", "geometrie_seule"] = "complet",
    strategie_geom: Literal["multipart", "eclate"] = STRATEGIE_GEOM_DEFAUT,  # type: ignore[assignment]
) -> dict[str, Any]:
    projet = _projet_or_404(projet_id)
    try:
        features = hist.features_ug(projet_id)
        cats = hist.categories_codes()
        payload = prepare_export_payload(
            projet, features, mode=mode, strategie=strategie_geom
        )
        last = hist.dernier_verse(projet_id)
        rapport = controler(
            projet=projet,
            geoms=payload["meta_geoms"],
            categories_ok=cats,
            last_verse=last,
            current_geom_hash=payload["geom_hash"],
            mode=mode,
            strategie_geom=strategie_geom,
            nb_parties=payload["nb_parties"],
        )
        rapport.surface_ha = payload["surface_ha"]
        rapport.nb_polygones = payload["nb_parties"]
        data = rapport.to_dict()
        data["has_versement"] = last is not None
        data["strategie_geom"] = strategie_geom
        data["mode"] = mode
        return data
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/apercu")
def apercu(
    projet_id: UUID,
    mode: Literal["complet", "geometrie_seule"] = "complet",
    strategie_geom: Literal["multipart", "eclate"] = STRATEGIE_GEOM_DEFAUT,  # type: ignore[assignment]
) -> dict[str, Any]:
    projet = _projet_or_404(projet_id)
    try:
        features = hist.features_ug(projet_id)
        payload = prepare_export_payload(
            projet, features, mode=mode, strategie=strategie_geom
        )
        return {
            "attributs": payload["attributs"],
            "srid": payload["srid"],
            "srid_label": payload["srid_label"],
            "nb_polygones": payload["nb_parties"],
            "surface_ha": payload["surface_ha"],
            "geom_hash": payload["geom_hash"],
            "strategie_geom": strategie_geom,
            "mode": mode,
        }
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post("/export")
def exporter(projet_id: UUID, body: ExportRequest) -> dict[str, Any]:
    projet = _projet_or_404(projet_id)
    try:
        features = hist.features_ug(projet_id)
        cats = hist.categories_codes()
        payload = prepare_export_payload(
            projet, features, mode=body.mode, strategie=body.strategie_geom
        )
        last = hist.dernier_verse(projet_id)
        rapport = controler(
            projet=projet,
            geoms=payload["meta_geoms"],
            categories_ok=cats,
            last_verse=last,
            current_geom_hash=payload["geom_hash"],
            mode=body.mode,
            strategie_geom=body.strategie_geom,
            nb_parties=payload["nb_parties"],
        )
        if not rapport.peut_exporter:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Export impossible : contrôles bloquants.",
                    "bloquants": rapport.to_dict()["bloquants"],
                },
            )

        # Confirmation doublon W07
        has_w07 = any(w.code == "W07" for w in rapport.avertissements)
        if has_w07 and not body.confirmer_doublon:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "message": "Géométrie déjà versée — confirmez le ré-export (risque de doublon).",
                    "code": "W07",
                },
            )

        if not payload["srid"] or not payload["polys_proj"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Géométrie ou SCR manquant.")

        gdf = build_geodataframe(
            payload["polys_proj"],
            payload["attributs"],
            strategie=body.strategie_geom,
        )
        basename = make_basename(
            str(projet.get("nom") or "projet"),
            str(projet.get("geomce_nom") or projet.get("nom") or "mesure"),
        )
        out_dir = hist.STORAGE_ROOT / str(projet_id)
        zip_path = write_zip(gdf, srid=int(payload["srid"]), basename=basename, out_dir=out_dir)

        row = hist.inserer_export(
            projet_id=projet_id,
            mode=body.mode,
            strategie_geom=body.strategie_geom,
            srid=int(payload["srid"]),
            nb_polygones=payload["nb_parties"],
            surface_ha=payload["surface_ha"],
            geom_hash=payload["geom_hash"],
            attributs=payload["attributs"],
            nom_fichier=f"{basename}.zip",
            storage_path=str(zip_path),
        )
        return {
            **row,
            "download_url": f"/api/exports-geomce/{row['id']}/download",
            "avertissements": rapport.to_dict()["avertissements"],
            "mode_emploi": {
                "etapes": [
                    "Dans GéoMCE : Mesure → Importer une mesure",
                    "Affilier le projet et la procédure (libellés ci-dessous)",
                    "Parcourir et sélectionner le ZIP généré",
                ],
                "projet_libelle": projet.get("geomce_projet_libelle") or projet.get("nom"),
                "procedure_libelle": projet.get("geomce_procedure_libelle") or projet.get("type_procedure"),
                "rappel_eclate": body.strategie_geom == "eclate" and payload["nb_parties"] > 1,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/historique")
def historique(projet_id: UUID) -> list[dict[str, Any]]:
    _projet_or_404(projet_id)
    try:
        return hist.lister_historique(projet_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


# Routes hors préfixe projet
router_exports = APIRouter(prefix="/exports-geomce", tags=["geomce"])


@router_exports.patch("/{export_id}")
def patch_export(export_id: UUID, body: ExportPatch) -> dict[str, Any]:
    try:
        existing = hist.get_export(export_id)
        if not existing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Export introuvable")
        return hist.patch_export(
            export_id,
            statut=body.statut,
            verse_le=body.verse_le,
            commentaire=body.commentaire,
            verrouiller_nom_projet_id=existing["projet_id"] if body.statut == "verse" else None,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router_exports.get("/{export_id}/download")
def download_export(export_id: UUID) -> FileResponse:
    row = hist.get_export(export_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Export introuvable")
    path = Path(row["storage_path"])
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Fichier introuvable sur le disque")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=row["nom_fichier"],
    )
