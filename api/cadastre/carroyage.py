"""Carroyage adaptatif récursif — contourne la limite ~5000 parcelles / appel IGN.

Inspiré d'un ETL WFS (subdivision en 4 quand une tuile sature).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .ign_client import (
    LIMIT_CAP_TUILE,
    MAX_CARROYAGE_DEPTH,
    CadastreIgnError,
    _prepare_query_geom,
    fetch_parcelles_intersectant_meta,
    probe_number_matched,
)

logger = logging.getLogger(__name__)


def _dedupe_idu(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties") or {}
        idu = str(props.get("idu") or feat.get("id") or "")
        if not idu or idu in seen:
            continue
        seen.add(idu)
        uniq.append(feat)
    return uniq


def _subdivide_bbox(geom: BaseGeometry) -> list[BaseGeometry]:
    """Découpe l'envelope en 4 quadrants (NW, NE, SW, SE)."""
    minx, miny, maxx, maxy = geom.bounds
    mx = (minx + maxx) / 2.0
    my = (miny + maxy) / 2.0
    return [
        box(minx, my, mx, maxy),  # NW
        box(mx, my, maxx, maxy),  # NE
        box(minx, miny, mx, my),  # SW
        box(mx, miny, maxx, my),  # SE
    ]


def fetch_parcelles_carroyage(
    geom_query_4326: BaseGeometry,
    *,
    depth: int = 0,
    pause_s: float = 0.15,
) -> list[dict[str, Any]]:
    """Fetch récursif : si numberMatched > LIMIT_CAP_TUILE → 4 sous-bbox.

    Ne lève pas sur le plafond 5000 d'une tuile : on subdivise jusqu'à
    ``MAX_CARROYAGE_DEPTH``. Dédup idu en sortie.
    """
    if geom_query_4326 is None or geom_query_4326.is_empty:
        return []

    geom = _prepare_query_geom(geom_query_4326)
    indent = "  " * depth
    label = f"carroyage[lvl={depth}]"

    matched = probe_number_matched(geom, label=label)
    logger.info(
        "%s[cadastre] %s — numberMatched=%s | cap_tuile=%s | bounds=%s",
        indent,
        label,
        matched,
        LIMIT_CAP_TUILE,
        tuple(round(c, 6) for c in geom.bounds),
    )

    if matched == 0:
        return []

    if matched <= LIMIT_CAP_TUILE:
        features, _ = fetch_parcelles_intersectant_meta(
            geom,
            max_matched=None,  # déjà sous le cap
            label=f"{label}:download",
        )
        logger.info(
            "%s[cadastre] %s — téléchargé %s parcelles (uniques après page)",
            indent,
            label,
            len(features),
        )
        return features

    if depth >= MAX_CARROYAGE_DEPTH:
        raise CadastreIgnError(
            f"Carroyage : profondeur max {MAX_CARROYAGE_DEPTH} atteinte "
            f"(numberMatched={matched} encore > {LIMIT_CAP_TUILE})."
        )

    logger.info(
        "%s[cadastre] %s — saturation (%s > %s) → subdivision en 4",
        indent,
        label,
        matched,
        LIMIT_CAP_TUILE,
    )
    acc: list[dict[str, Any]] = []
    for i, sub in enumerate(_subdivide_bbox(geom)):
        if pause_s > 0 and i > 0:
            time.sleep(pause_s)
        acc.extend(fetch_parcelles_carroyage(sub, depth=depth + 1, pause_s=pause_s))

    uniq = _dedupe_idu(acc)
    logger.info(
        "%s[cadastre] %s — fusion | brutes=%s | uniques=%s",
        indent,
        label,
        len(acc),
        len(uniq),
    )
    return uniq
