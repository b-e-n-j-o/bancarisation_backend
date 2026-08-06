"""Client API Carto IGN — module cadastre (parcelles PCI)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union
from pyproj import Transformer

logger = logging.getLogger(__name__)

APICARTO_PARCELLE_URL = "https://apicarto.ign.fr/api/cadastre/parcelle"

# Buffer par UG (méthode de repli — méthode 2)
DEFAULT_BUFFER_M = 100.0
FALLBACK_BUFFER_M = 200.0

# Marge sur la bbox englobant toutes les UG (méthode préférée)
BBOX_MARGIN_M = 80.0

# Cap soft IGN par appel / tuile — au-delà → carroyage
LIMIT_CAP_TUILE = 4950
MAX_PARCELLES_PAR_APPEL = LIMIT_CAP_TUILE  # alias historique

# Au-delà de ce hit initial sur la bbox projet → méthode 2 (buffer UG)
MAX_PARCELLES_ZONE = 20_000

MAX_CARROYAGE_DEPTH = 8
PAGE_LIMIT = 1000
MAX_PAGES = 5  # 5 × 1000 = 5000 (≥ LIMIT_CAP_TUILE)

_TO_3857 = Transformer.from_crs(4326, 3857, always_xy=True).transform
_TO_4326 = Transformer.from_crs(3857, 4326, always_xy=True).transform


class CadastreIgnError(Exception):
    pass


class CadastreTooManyError(CadastreIgnError):
    """Zone trop dense (> MAX_PARCELLES_ZONE) → repli buffer par UG."""

    def __init__(self, matched: int, limit: int = MAX_PARCELLES_ZONE):
        self.matched = matched
        self.limit = limit
        super().__init__(
            f"Trop de parcelles IGN ({matched} > {limit}) pour la zone demandée."
        )


def buffer_geom_3857(geom_3857: BaseGeometry, buffer_m: float = DEFAULT_BUFFER_M) -> BaseGeometry:
    """Buffer métrique depuis une géométrie déjà en EPSG:3857 → résultat WGS84."""
    if geom_3857 is None or geom_3857.is_empty:
        raise CadastreIgnError("Géométrie vide pour buffer cadastre.")
    buffered = geom_3857.buffer(float(buffer_m)).simplify(2.0, preserve_topology=True)
    return transform(_TO_4326, buffered)


def bbox_elargie_3857(geom_3857: BaseGeometry, margin_m: float = BBOX_MARGIN_M) -> BaseGeometry:
    """Envelope + légère marge métrique → WGS84 (pour fetch zone projet)."""
    if geom_3857 is None or geom_3857.is_empty:
        raise CadastreIgnError("Géométrie vide pour bbox cadastre.")
    boxed = geom_3857.envelope.buffer(float(margin_m))
    return transform(_TO_4326, boxed)


def _prepare_query_geom(geom_query_4326: BaseGeometry) -> BaseGeometry:
    if geom_query_4326.geom_type == "GeometryCollection":
        parts = [g for g in geom_query_4326.geoms if not g.is_empty]
        if not parts:
            raise CadastreIgnError("GeometryCollection vide.")
        geom_query_4326 = unary_union(parts)
    if geom_query_4326.geom_type not in ("Polygon", "MultiPolygon"):
        geom_query_4326 = geom_query_4326.envelope
    return geom_query_4326


def probe_number_matched(
    geom_query_4326: BaseGeometry,
    *,
    timeout_s: float = 45.0,
    label: str = "probe",
) -> int:
    """1er hit léger (_limit=1) pour connaître numberMatched sans tout télécharger."""
    if geom_query_4326 is None or geom_query_4326.is_empty:
        return 0
    geom_query_4326 = _prepare_query_geom(geom_query_4326)
    geom_payload = mapping(geom_query_4326)
    with httpx.Client(timeout=httpx.Timeout(15.0, read=timeout_s)) as client:
        try:
            resp = client.post(
                APICARTO_PARCELLE_URL,
                json={"geom": geom_payload, "_limit": 1, "_start": 0},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise CadastreIgnError(f"API Carto cadastre indisponible (probe): {exc}") from exc
        data = resp.json()
        matched = int(data.get("numberMatched") or data.get("totalFeatures") or 0)
    logger.info(
        "[cadastre] %s — probe numberMatched=%s | bounds=%s",
        label,
        matched,
        tuple(round(c, 6) for c in geom_query_4326.bounds),
    )
    return matched


def fetch_parcelles_intersectant_meta(
    geom_query_4326: BaseGeometry,
    *,
    timeout_s: float = 60.0,
    max_matched: int | None = LIMIT_CAP_TUILE,
    label: str = "ign",
) -> tuple[list[dict[str, Any]], int]:
    """Fetch IGN + ``numberMatched`` du 1er appel.

    Si ``max_matched`` est défini et ``numberMatched`` le dépasse → ``CadastreTooManyError``.
    """
    if geom_query_4326 is None or geom_query_4326.is_empty:
        return [], 0

    geom_query_4326 = _prepare_query_geom(geom_query_4326)
    geom_payload = mapping(geom_query_4326)
    bounds = geom_query_4326.bounds
    features: list[dict[str, Any]] = []
    start = 0
    matched_total = 0

    logger.info(
        "[cadastre] %s — requête IGN démarrée | max_matched=%s | bounds_wgs84=%s",
        label,
        max_matched,
        tuple(round(c, 6) for c in bounds),
    )

    with httpx.Client(timeout=httpx.Timeout(15.0, read=timeout_s)) as client:
        for page_idx in range(MAX_PAGES):
            try:
                resp = client.post(
                    APICARTO_PARCELLE_URL,
                    json={"geom": geom_payload, "_limit": PAGE_LIMIT, "_start": start},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error(
                    "[cadastre] %s — erreur HTTP IGN page=%s start=%s: %s",
                    label,
                    page_idx,
                    start,
                    exc,
                )
                raise CadastreIgnError(f"API Carto cadastre indisponible: {exc}") from exc

            data = resp.json()
            matched_total = int(data.get("numberMatched") or data.get("totalFeatures") or 0)
            returned = int(data.get("numberReturned") or len(data.get("features") or []))

            if page_idx == 0:
                logger.info(
                    "[cadastre] %s — 1er appel IGN | numberMatched=%s | numberReturned=%s | "
                    "limite=%s | dépasse=%s",
                    label,
                    matched_total,
                    returned,
                    max_matched,
                    (max_matched is not None and matched_total > max_matched),
                )

            if (
                max_matched is not None
                and matched_total > max_matched
                and start == 0
            ):
                logger.warning(
                    "[cadastre] %s — SEUIL DÉPASSÉ (%s > %s) → CadastreTooManyError",
                    label,
                    matched_total,
                    max_matched,
                )
                raise CadastreTooManyError(matched_total, max_matched)

            batch = data.get("features") or []
            if not isinstance(batch, list):
                break
            features.extend(batch)
            start += returned
            if returned == 0 or start >= (matched_total or returned) or returned < PAGE_LIMIT:
                break

    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties") or {}
        idu = str(props.get("idu") or feat.get("id") or "")
        if not idu or idu in seen:
            continue
        seen.add(idu)
        uniq.append(feat)

    logger.info(
        "[cadastre] %s — fetch terminé | numberMatched_annoncé=%s | parcelles_uniques=%s",
        label,
        matched_total,
        len(uniq),
    )
    return uniq, matched_total


def fetch_parcelles_autour_geom_3857(
    geom_3857: BaseGeometry,
    *,
    buffer_m: float = DEFAULT_BUFFER_M,
    max_matched: int | None = None,
    label: str = "buffer_ug",
) -> list[dict[str, Any]]:
    """Buffer métrique + appel IGN (méthode par UG)."""
    query = buffer_geom_3857(geom_3857, buffer_m=buffer_m)
    features, _ = fetch_parcelles_intersectant_meta(
        query, max_matched=max_matched, label=label,
    )
    return features


def fetch_parcelles_bbox_projet_3857(
    geom_3857: BaseGeometry,
    *,
    margin_m: float = BBOX_MARGIN_M,
) -> tuple[list[dict[str, Any]], int, str]:
    """Bbox projet élargie → (features, numberMatched initial, sous-méthode).

    - matched ≤ LIMIT_CAP_TUILE → download direct
    - LIMIT_CAP_TUILE < matched ≤ MAX_PARCELLES_ZONE → carroyage adaptatif
    - matched > MAX_PARCELLES_ZONE → CadastreTooManyError (repli méthode 2)
    """
    from .carroyage import fetch_parcelles_carroyage

    query = bbox_elargie_3857(geom_3857, margin_m=margin_m)
    label = f"bbox_projet(margin={margin_m:.0f}m)"
    matched = probe_number_matched(query, label=f"{label}:probe")

    if matched > MAX_PARCELLES_ZONE:
        raise CadastreTooManyError(matched, MAX_PARCELLES_ZONE)

    if matched <= LIMIT_CAP_TUILE:
        features, _ = fetch_parcelles_intersectant_meta(
            query,
            max_matched=None,
            label=f"{label}:direct",
        )
        return features, matched, "bbox_direct"

    logger.info(
        "[cadastre] %s — %s parcelles (entre %s et %s) → carroyage adaptatif",
        label,
        matched,
        LIMIT_CAP_TUILE,
        MAX_PARCELLES_ZONE,
    )
    features = fetch_parcelles_carroyage(query)
    return features, matched, "bbox_carroyage"
