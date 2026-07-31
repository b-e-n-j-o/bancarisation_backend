"""Client Sentinel Hub Catalog + Process API (Sentinel-2 L2A)."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

import httpx

from api.satellite.aoi import OUTPUT_EPSG, RESOLUTION_M, PaddedAoi
from api.satellite.auth import SentinelAuthError, auth_headers, get_sentinel_token

CATALOG_URL = "https://services.sentinel-hub.com/api/v1/catalog/1.0.0/search"
PROCESS_URL = "https://services.sentinel-hub.com/api/v1/process"
COLLECTION = "sentinel-2-l2a"
MAX_CLOUD_COVER = 30

EVALSCRIPT_BANDS = """//VERSION=3
function setup() {
  return {
    input: [{
      bands: ["B02", "B03", "B04", "B08", "SCL", "dataMask"],
      units: ["REFLECTANCE", "REFLECTANCE", "REFLECTANCE", "REFLECTANCE", "DN", "DN"]
    }],
    output: { bands: 6, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(s) {
  return [s.B02, s.B03, s.B04, s.B08, s.SCL, s.dataMask];
}
"""

EVALSCRIPT_TRUECOLOR = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B03", "B02"] }],
    output: { bands: 3, sampleType: "UINT8" }
  };
}
function evaluatePixel(s) {
  return [255 * 2.5 * s.B04, 255 * 2.5 * s.B03, 255 * 2.5 * s.B02];
}
"""


class SentinelServiceError(Exception):
    pass


def _request_with_auth_retry(
    method: str,
    url: str,
    *,
    json_body: Optional[dict] = None,
    accept: Optional[str] = None,
    timeout: float = 120.0,
) -> httpx.Response:
    headers = auth_headers()
    if accept:
        headers["Accept"] = accept

    with httpx.Client(timeout=timeout) as client:
        resp = client.request(method, url, json=json_body, headers=headers)
        if resp.status_code == 401:
            get_sentinel_token(force_refresh=True)
            headers = auth_headers()
            if accept:
                headers["Accept"] = accept
            resp = client.request(method, url, json=json_body, headers=headers)
        return resp


def list_scenes(
    geometry_4326: dict[str, Any],
    start: date,
    end: date,
    *,
    max_cloud_cover: float = MAX_CLOUD_COVER,
) -> list[dict[str, Any]]:
    """Catalogue Sentinel-2 L2A intersectant l'AOI (nuages <= max_cloud_cover %)."""
    body = {
        "collections": [COLLECTION],
        "intersects": geometry_4326,
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "limit": 100,
        "filter": {
            "op": "<=",
            "args": [{"property": "eo:cloud_cover"}, max_cloud_cover],
        },
        "filter-lang": "cql2-json",
        "fields": {
            "include": ["id", "properties.datetime", "properties.eo:cloud_cover"]
        },
    }
    try:
        resp = _request_with_auth_retry("POST", CATALOG_URL, json_body=body, timeout=60.0)
    except SentinelAuthError:
        raise
    except httpx.HTTPError as exc:
        raise SentinelServiceError(f"Catalog API réseau: {exc}") from exc

    if resp.status_code != 200:
        raise SentinelServiceError(
            f"Catalog API {resp.status_code}: {resp.text[:800]}"
        )
    return resp.json().get("features", [])


def _process_body(
    aoi: PaddedAoi,
    day: date,
    evalscript: str,
    mime: str,
) -> dict[str, Any]:
    return {
        "input": {
            "bounds": {
                "geometry": aoi.geometry_3035,
                "properties": {
                    "crs": f"http://www.opengis.net/def/crs/EPSG/0/{OUTPUT_EPSG}"
                },
            },
            "data": [
                {
                    "type": COLLECTION,
                    "dataFilter": {
                        "timeRange": {
                            "from": f"{day.isoformat()}T00:00:00Z",
                            "to": f"{day.isoformat()}T23:59:59Z",
                        },
                        "maxCloudCoverage": MAX_CLOUD_COVER,
                        "mosaickingOrder": "leastCC",
                    },
                }
            ],
        },
        "output": {
            "resx": RESOLUTION_M,
            "resy": RESOLUTION_M,
            "responses": [
                {"identifier": "default", "format": {"type": mime}}
            ],
        },
        "evalscript": evalscript,
    }


def fetch_truecolor_png(aoi: PaddedAoi, day: date) -> bytes:
    body = _process_body(aoi, day, EVALSCRIPT_TRUECOLOR, "image/png")
    try:
        resp = _request_with_auth_retry(
            "POST",
            PROCESS_URL,
            json_body=body,
            accept="image/png",
            timeout=180.0,
        )
    except SentinelAuthError:
        raise
    except httpx.HTTPError as exc:
        raise SentinelServiceError(f"Process API PNG réseau: {exc}") from exc

    if resp.status_code != 200:
        raise SentinelServiceError(
            f"Process API PNG {resp.status_code}: {resp.text[:800]}"
        )
    return resp.content


def fetch_bands_tiff(aoi: PaddedAoi, day: date) -> bytes:
    body = _process_body(aoi, day, EVALSCRIPT_BANDS, "image/tiff")
    try:
        resp = _request_with_auth_retry(
            "POST",
            PROCESS_URL,
            json_body=body,
            accept="image/tiff",
            timeout=180.0,
        )
    except SentinelAuthError:
        raise
    except httpx.HTTPError as exc:
        raise SentinelServiceError(f"Process API TIFF réseau: {exc}") from exc

    if resp.status_code != 200:
        raise SentinelServiceError(
            f"Process API TIFF {resp.status_code}: {resp.text[:800]}"
        )
    return resp.content


def best_scene_for_day(
    scenes: list[dict[str, Any]], day: date
) -> Optional[dict[str, Any]]:
    """Scène du jour avec le moins de nuages (parmi celles du catalogue)."""
    day_s = day.isoformat()
    matches = [
        f
        for f in scenes
        if str((f.get("properties") or {}).get("datetime", "")).startswith(day_s)
    ]
    if not matches:
        return None

    def cloud(f: dict) -> float:
        return float((f.get("properties") or {}).get("eo:cloud_cover") or 100)

    return min(matches, key=cloud)
