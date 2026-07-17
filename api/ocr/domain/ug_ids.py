"""Normalisation des identifiants d'unités de gestion.

Convention : géométrie.ug_id ∈ *.ug_ids[]
  "UG 1" / "UG1" / "ug1" → "ug1"
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")


def normalize_ug_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = _NON_ALNUM.sub("", str(raw).strip()).lower()
    return cleaned or None


def normalize_ug_ids(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if not values:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        n = normalize_ug_id(v)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out
