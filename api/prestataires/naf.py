"""Libellés NAF (nomenclature INSEE), indexés par code."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_FICHIER = Path(__file__).with_name("insee.codenaf.json")


@lru_cache(maxsize=1)
def _table() -> dict[str, str]:
    with _FICHIER.open(encoding="utf-8") as f:
        data = json.load(f)
    return {str(k): str(v) for k, v in data.items()}


def libelle_naf(code: str | None) -> str | None:
    if not code:
        return None
    return _table().get(code.strip())
