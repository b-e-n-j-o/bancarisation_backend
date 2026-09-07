"""Similitude 2 points (translation + rotation + échelle) et application PostGIS.

geom_local n'est jamais modifié. geom (2154) et geom_3857 sont recalculés
via ST_Affine à partir des paramètres stockés sur plan_cao.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from api.carto.persist import PlanCaoError

# Distance minimale entre les deux points source (unités du dessin).
_DIST_MIN = 1e-6


class CalageError(PlanCaoError):
    pass


def similitude_deux_points(
    src1: Sequence[float],
    dst1: Sequence[float],
    src2: Sequence[float],
    dst2: Sequence[float],
) -> dict[str, float]:
    """Helmert 4 paramètres : src (repère dessin) → dst (Lambert-93)."""
    x1, y1 = float(src1[0]), float(src1[1])
    x2, y2 = float(src2[0]), float(src2[1])
    X1, Y1 = float(dst1[0]), float(dst1[1])
    X2, Y2 = float(dst2[0]), float(dst2[1])

    dx, dy = x2 - x1, y2 - y1
    dX, dY = X2 - X1, Y2 - Y1
    n_src = math.hypot(dx, dy)
    n_dst = math.hypot(dX, dY)
    if n_src < _DIST_MIN:
        raise CalageError("Les deux points du plan CAO sont trop proches.")
    if n_dst < _DIST_MIN:
        raise CalageError("Les deux points de la carte sont trop proches.")

    echelle = n_dst / n_src
    rotation = math.atan2(dY, dX) - math.atan2(dy, dx)
    c, s = math.cos(rotation), math.sin(rotation)
    rx = echelle * (x1 * c - y1 * s)
    ry = echelle * (x1 * s + y1 * c)
    return {
        "tx": X1 - rx,
        "ty": Y1 - ry,
        "rotation_rad": rotation,
        "echelle": echelle,
    }


def coeffs_affine(tx: float, ty: float, rotation_rad: float, echelle: float) -> tuple[float, float, float, float, float, float]:
    """Coefficients ST_Affine 2D : x' = a x + b y + xoff ; y' = d x + e y + yoff."""
    c, s = math.cos(rotation_rad), math.sin(rotation_rad)
    a = echelle * c
    b = -echelle * s
    d = echelle * s
    e = echelle * c
    return a, b, d, e, tx, ty


def paires_depuis_body(paires: Sequence[dict[str, Any]]) -> tuple[list[float], list[float], list[float], list[float]]:
    if len(paires) < 2:
        raise CalageError("Deux couples de points sont nécessaires.")
    try:
        s1 = paires[0]["src"]
        d1 = paires[0]["dst_2154"]
        s2 = paires[1]["src"]
        d2 = paires[1]["dst_2154"]
        if len(s1) < 2 or len(d1) < 2 or len(s2) < 2 or len(d2) < 2:
            raise KeyError
    except (KeyError, TypeError) as exc:
        raise CalageError("Chaque paire doit avoir src [x, y] et dst_2154 [X, Y].") from exc
    return s1, d1, s2, d2
