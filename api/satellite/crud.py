"""Persistence satellite_captures + upload Storage Supabase."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from dotenv import load_dotenv
from supabase import Client, create_client

from api.satellite.aoi import build_padded_aoi
from api.satellite.auth import SentinelAuthError
from api.satellite.sentinel import (
    SentinelServiceError,
    best_scene_for_day,
    fetch_bands_tiff,
    fetch_truecolor_png,
    list_scenes,
)

load_dotenv()

BUCKET = "documents-projet"
TABLE = "satellite_captures"


class SatelliteCrudError(Exception):
    pass


def _supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SatelliteCrudError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY manquants."
        )
    return create_client(url, key)


def _safe_segment(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", value.strip()) or "ug"


def _upload_bytes(
    projet_id: UUID,
    ug_id: str,
    acquisition_date: date,
    filename: str,
    content: bytes,
    content_type: str,
) -> str:
    """Upload direct Storage — chemin {projet}/satellite/{ug}/{date}/{fichier}."""
    client = _supabase()
    ug_seg = _safe_segment(ug_id)
    day_s = acquisition_date.isoformat()
    bucket_path = f"{projet_id}/satellite/{ug_seg}/{day_s}/{filename}"
    try:
        client.storage.from_(BUCKET).upload(
            path=bucket_path,
            file=content,
            file_options={
                "content-type": content_type,
                "upsert": "true",
            },
        )
        client.storage.from_(BUCKET).download(bucket_path)
    except Exception as exc:
        raise SatelliteCrudError(f"Upload Storage: {exc}") from exc
    return bucket_path


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("id", "projet_id", "document_id_png", "document_id_tif"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    if out.get("acquisition_date") is not None:
        ad = out["acquisition_date"]
        out["acquisition_date"] = ad if isinstance(ad, str) else ad.isoformat()
    bbox = out.get("bbox_4326")
    if isinstance(bbox, str):
        import json

        try:
            out["bbox_4326"] = json.loads(bbox)
        except json.JSONDecodeError:
            pass
    return out


def _first_row(resp: Any) -> Optional[dict[str, Any]]:
    """Supabase maybe_single / limit(1) → ligne ou None (resp parfois None)."""
    if resp is None:
        return None
    data = getattr(resp, "data", None)
    if data is None:
        return None
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], dict) else None
    if isinstance(data, dict):
        return data
    return None


def get_capture(
    projet_id: UUID, ug_id: str, acquisition_date: date
) -> Optional[dict[str, Any]]:
    client = _supabase()
    try:
        resp = (
            client.schema("bancarisation")
            .table(TABLE)
            .select("*")
            .eq("projet_id", str(projet_id))
            .eq("ug_id", ug_id)
            .eq("acquisition_date", acquisition_date.isoformat())
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise SatelliteCrudError(
            f"Lecture capture: {exc}. "
            "Vérifier que la table bancarisation.satellite_captures existe "
            "(sql/001_satellite_captures.sql)."
        ) from exc
    row = _first_row(resp)
    return _row_to_dict(row) if row else None


def get_capture_by_id(
    projet_id: UUID, capture_id: UUID
) -> Optional[dict[str, Any]]:
    client = _supabase()
    try:
        resp = (
            client.schema("bancarisation")
            .table(TABLE)
            .select("*")
            .eq("projet_id", str(projet_id))
            .eq("id", str(capture_id))
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise SatelliteCrudError(
            f"Lecture capture: {exc}. "
            "Vérifier que la table bancarisation.satellite_captures existe "
            "(sql/001_satellite_captures.sql)."
        ) from exc
    row = _first_row(resp)
    return _row_to_dict(row) if row else None


def list_captures(
    projet_id: UUID, *, ug_id: Optional[str] = None
) -> list[dict[str, Any]]:
    client = _supabase()
    try:
        q = (
            client.schema("bancarisation")
            .table(TABLE)
            .select("*")
            .eq("projet_id", str(projet_id))
            .eq("status", "ready")
            .order("acquisition_date", desc=True)
        )
        if ug_id:
            q = q.eq("ug_id", ug_id)
        resp = q.execute()
    except Exception as exc:
        raise SatelliteCrudError(f"Liste captures: {exc}") from exc
    rows = resp.data or []
    return [_row_to_dict(r) for r in rows if isinstance(r, dict)]


def list_available_scenes(
    projet_id: UUID,
    ug_id: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    aoi = build_padded_aoi(projet_id, ug_id)
    features = list_scenes(aoi.geometry_4326, start, end)
    scenes: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for feat in sorted(
        features, key=lambda f: (f.get("properties") or {}).get("datetime") or ""
    ):
        props = feat.get("properties") or {}
        dt = str(props.get("datetime") or "")
        day = dt[:10]
        if not day or day in seen_dates:
            continue
        seen_dates.add(day)
        scenes.append(
            {
                "date": day,
                "datetime": dt,
                "scene_id": feat.get("id"),
                "cloud_cover": props.get("eo:cloud_cover"),
            }
        )
    return scenes


def _download_storage(bucket_path: str) -> bytes:
    client = _supabase()
    try:
        raw = client.storage.from_(BUCKET).download(bucket_path)
    except Exception as exc:
        raise SatelliteCrudError(f"Téléchargement Storage: {exc}") from exc
    if isinstance(raw, memoryview):
        return raw.tobytes()
    if isinstance(raw, (bytearray, bytes)):
        return bytes(raw)
    return bytes(raw)


def download_preview(capture: dict[str, Any]) -> tuple[bytes, str]:
    path = capture.get("bucket_path_png")
    if not path:
        raise SatelliteCrudError("Preview PNG absente pour cette capture.")
    return _download_storage(path), "image/png"


def download_tif(capture: dict[str, Any]) -> tuple[bytes, str]:
    path = capture.get("bucket_path_tif")
    if not path:
        raise SatelliteCrudError("GeoTIFF absent pour cette capture.")
    return _download_storage(path), "image/tiff"


def ensure_capture(
    projet_id: UUID, ug_id: str, acquisition_date: date
) -> tuple[dict[str, Any], bool]:
    """
    Retourne (capture, created).
    Si une capture ready existe déjà → pas d'appel Process API.
    """
    existing = get_capture(projet_id, ug_id, acquisition_date)
    if existing and existing.get("status") == "ready":
        return existing, False

    aoi = build_padded_aoi(projet_id, ug_id)

    # Métadonnées scène (optionnel — n'échoue pas le fetch)
    scene_id: Optional[str] = None
    cloud_cover: Optional[float] = None
    try:
        scenes = list_scenes(aoi.geometry_4326, acquisition_date, acquisition_date)
        best = best_scene_for_day(scenes, acquisition_date)
        if best:
            scene_id = best.get("id")
            cc = (best.get("properties") or {}).get("eo:cloud_cover")
            cloud_cover = float(cc) if cc is not None else None
    except (SentinelServiceError, SentinelAuthError):
        pass

    try:
        png_bytes = fetch_truecolor_png(aoi, acquisition_date)
        tif_bytes = fetch_bands_tiff(aoi, acquisition_date)
    except (SentinelServiceError, SentinelAuthError) as exc:
        raise SatelliteCrudError(str(exc)) from exc

    day_s = acquisition_date.isoformat()
    path_png = _upload_bytes(
        projet_id,
        ug_id,
        acquisition_date,
        f"s2_l2a_{day_s}_truecolor.png",
        png_bytes,
        "image/png",
    )
    path_tif = _upload_bytes(
        projet_id,
        ug_id,
        acquisition_date,
        f"s2_l2a_{day_s}_bands.tif",
        tif_bytes,
        "image/tiff",
    )

    payload = {
        "projet_id": str(projet_id),
        "ug_id": ug_id,
        "acquisition_date": day_s,
        "scene_id": scene_id,
        "cloud_cover": cloud_cover,
        "bbox_4326": aoi.bbox_4326,
        "epsg": aoi.epsg,
        "resolution_m": aoi.resolution_m,
        "width_px": aoi.width_px,
        "height_px": aoi.height_px,
        "bucket_path_png": path_png,
        "bucket_path_tif": path_tif,
        "document_id_png": None,
        "document_id_tif": None,
        "status": "ready",
        "error_message": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    client = _supabase()
    try:
        if existing:
            resp = (
                client.schema("bancarisation")
                .table(TABLE)
                .update(payload)
                .eq("id", existing["id"])
                .execute()
            )
        else:
            resp = (
                client.schema("bancarisation")
                .table(TABLE)
                .insert(payload)
                .execute()
            )
    except Exception as exc:
        raise SatelliteCrudError(
            f"Insertion capture: {exc}. "
            "Vérifier que la table bancarisation.satellite_captures existe "
            "(sql/001_satellite_captures.sql) et est exposée dans l'API Supabase."
        ) from exc

    row = _first_row(resp)
    if row:
        return _row_to_dict(row), True
    refreshed = get_capture(projet_id, ug_id, acquisition_date)
    if not refreshed:
        raise SatelliteCrudError("Insertion capture échouée (aucune ligne renvoyée).")
    return refreshed, True


def enrich_capture_urls(capture: dict[str, Any], projet_id: UUID) -> dict[str, Any]:
    """Ajoute les URLs relatives preview / tif pour le front."""
    cid = capture.get("id")
    base = f"/api/projets/{projet_id}/satellite/captures/{cid}"
    out = dict(capture)
    out["preview_url"] = f"{base}/preview" if cid else None
    out["tif_url"] = f"{base}/tif" if cid else None
    return out
