"""Module cadastre IGN — croisement autour des UG à l'ingestion + lecture carto."""

from .enrichissement import EnrichissementCadastreResult, enrichir_cadastre_projet
from .ign_client import (
    DEFAULT_BUFFER_M,
    FALLBACK_BUFFER_M,
    LIMIT_CAP_TUILE,
    MAX_PARCELLES_ZONE,
    CadastreIgnError,
)

__all__ = [
    "DEFAULT_BUFFER_M",
    "FALLBACK_BUFFER_M",
    "LIMIT_CAP_TUILE",
    "MAX_PARCELLES_ZONE",
    "CadastreIgnError",
    "EnrichissementCadastreResult",
    "enrichir_cadastre_projet",
]
