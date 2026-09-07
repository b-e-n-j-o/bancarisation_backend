"""WFS PCI Express (Géoplateforme) — hit puis fetch, sans persistance.

Cap 5 000 entités / requête : si le hit dépasse, on réduit la bbox
autour de son centre (chaque côté ÷ 2) et on rehit jusqu’à passer sous le cap.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WFS_URL = "https://data.geopf.fr/wfs/ows"
LAYER = "CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle"
BBOX_CRS = "urn:ogc:def:crs:OGC:1.3:CRS84"
CAP = 5000
MAX_REDUCTIONS = 16
TIMEOUT_S = 90.0

_NUMBER_MATCHED = re.compile(r'numberMatched="(\d+|unknown)"', re.I)

PROPS_GARDES = ("idu", "section", "numero", "contenance", "nom_com", "code_dep", "code_insee")


class PciWfsError(Exception):
    pass


def _bbox_ok(w: float, s: float, e: float, n: float) -> tuple[float, float, float, float]:
    west, east = (w, e) if w <= e else (e, w)
    south, north = (s, n) if s <= n else (n, s)
    if east - west < 1e-6 or north - south < 1e-6:
        raise PciWfsError("Rectangle trop petit — élargissez la zone.")
    if east - west > 4 or north - south > 4:
        raise PciWfsError("Zone trop vaste — zoomez sur le site (quelques kilomètres).")
    return west, south, east, north


def _reduire_moitie(w: float, s: float, e: float, n: float) -> tuple[float, float, float, float]:
    cx, cy = (w + e) / 2.0, (s + n) / 2.0
    hw, hh = (e - w) / 4.0, (n - s) / 4.0
    return cx - hw, cy - hh, cx + hw, cy + hh


def _parse_hits(xml: str) -> int | None:
    m = _NUMBER_MATCHED.search(xml)
    if not m:
        return None
    raw = m.group(1)
    if raw.lower() == "unknown":
        return None
    return int(raw)


def wfs_hits(bbox: tuple[float, float, float, float]) -> int | None:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": LAYER,
        "resultType": "hits",
        "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]},{BBOX_CRS}",
    }
    with httpx.Client(timeout=httpx.Timeout(20.0, read=TIMEOUT_S)) as client:
        resp = client.get(WFS_URL, params=params)
        if not resp.is_success:
            raise PciWfsError(f"WFS IGN indisponible (hit HTTP {resp.status_code}).")
        return _parse_hits(resp.text)


def wfs_fetch(bbox: tuple[float, float, float, float], count: int = CAP) -> dict[str, Any]:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": LAYER,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "count": str(count),
        "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]},{BBOX_CRS}",
    }
    with httpx.Client(timeout=httpx.Timeout(20.0, read=TIMEOUT_S)) as client:
        resp = client.get(WFS_URL, params=params)
        if not resp.is_success:
            raise PciWfsError(f"WFS IGN indisponible (fetch HTTP {resp.status_code}).")
        try:
            data = resp.json()
        except ValueError as exc:
            raise PciWfsError("Réponse WFS illisible (JSON attendu).") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise PciWfsError("Réponse WFS inattendue.")
    return data


def _alleger(fc: dict[str, Any]) -> list[dict[str, Any]]:
    sorties: list[dict[str, Any]] = []
    seen: set[str] = set()
    for feat in fc.get("features") or []:
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry")
        if not isinstance(geom, dict):
            continue
        props_in = feat.get("properties") or {}
        if not isinstance(props_in, dict):
            props_in = {}
        idu = str(props_in.get("idu") or feat.get("id") or "")
        if idu and idu in seen:
            continue
        if idu:
            seen.add(idu)
        else:
            idu = f"pci-{len(sorties)}"
        props = {k: props_in[k] for k in PROPS_GARDES if k in props_in and props_in[k] is not None}
        props["idu"] = idu
        props["label"] = f"{props.get('section') or ''} {props.get('numero') or ''}".strip() or idu
        sorties.append({
            "type": "Feature",
            "id": idu or len(sorties),
            "geometry": geom,
            "properties": props,
        })
    return sorties


def parcelles_pour_bbox(
    west: float,
    south: float,
    east: float,
    north: float,
) -> dict[str, Any]:
    bbox = _bbox_ok(west, south, east, north)
    hit_initial = wfs_hits(bbox)
    reductions = 0
    hit = hit_initial

    while hit is not None and hit > CAP and reductions < MAX_REDUCTIONS:
        bbox = _reduire_moitie(*bbox)
        reductions += 1
        hit = wfs_hits(bbox)
        logger.info(
            "[pci-wfs] réduction %s — hit=%s bbox=%s",
            reductions,
            hit,
            tuple(round(c, 5) for c in bbox),
        )

    if hit is None:
        fc = wfs_fetch(bbox)
        features = _alleger(fc)
        if len(features) >= CAP and reductions < MAX_REDUCTIONS:
            while len(features) >= CAP and reductions < MAX_REDUCTIONS:
                bbox = _reduire_moitie(*bbox)
                reductions += 1
                features = _alleger(wfs_fetch(bbox))
        hit = len(features)
        if hit_initial is None:
            hit_initial = hit
    else:
        if hit == 0:
            features = []
        else:
            features = _alleger(wfs_fetch(bbox, count=min(CAP, hit)))

    w, s, e, n = bbox
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "nb": len(features),
            "hit_initial": hit_initial,
            "hit_final": hit,
            "reductions": reductions,
            "cap": CAP,
            "bbox": {"west": w, "south": s, "east": e, "north": n},
            "persiste": False,
        },
    }
