"""Éclatement sûr des blocs / proxies DXF.

Façade : délègue à `eclater_avec_contexte` et ignore le contexte de bloc.
Préférer `api.carto.dxf_contexte.eclater_avec_contexte` dès qu'on a besoin
du nom de bloc, des ATTRIB ou de la couleur ByBlock.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from ezdxf.entities import DXFEntity

try:
    from api.carto.dxf_contexte import TYPES_A_ECLATER, eclater_avec_contexte
except ImportError:  # exécution CLI : python3 api/carto/dxf_eclater.py
    from dxf_contexte import TYPES_A_ECLATER, eclater_avec_contexte

__all__ = ["TYPES_A_ECLATER", "eclater_sur"]


def eclater_sur(entities: Iterable[DXFEntity]) -> Iterator[DXFEntity]:
    """Éclate blocs/proxies entité par entité ; une erreur n'arrête pas la suite."""
    for entity, _ctx in eclater_avec_contexte(entities):
        yield entity
