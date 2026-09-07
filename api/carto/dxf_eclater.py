"""Éclatement sûr des blocs / proxies DXF.

`ezdxf.disassemble.recursive_decompose` s'arrête dès qu'un ACAD_PROXY_ENTITY
a des graphiques proxy illisibles. Ici chaque entité est tentée isolément.
"""

from __future__ import annotations

import logging
from typing import Iterable, Iterator

from ezdxf.entities import DXFEntity

logger = logging.getLogger(__name__)

TYPES_A_ECLATER = frozenset({
    "INSERT",
    "ACAD_PROXY_ENTITY",
    "ACAD_TABLE",
    "DIMENSION",
    "LEADER",
    "MULTILEADER",
    "MLEADER",
    "MLINE",
})


def eclater_sur(entities: Iterable[DXFEntity]) -> Iterator[DXFEntity]:
    """Éclate blocs/proxies entité par entité ; une erreur n'arrête pas la suite."""
    for entity in entities:
        t = entity.dxftype()
        if t not in TYPES_A_ECLATER:
            yield entity
            continue
        try:
            if t == "INSERT" and getattr(entity, "mcount", 1) > 1:
                subs = list(entity.multi_insert())
            elif hasattr(entity, "virtual_entities"):
                subs = list(entity.virtual_entities())
            else:
                yield entity
                continue
        except Exception as exc:
            logger.warning(
                "éclatement %s impossible (%s) — entité conservée telle quelle",
                t,
                exc,
            )
            yield entity
            continue
        if not subs:
            yield entity
            continue
        yield from eclater_sur(subs)
