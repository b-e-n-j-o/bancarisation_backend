"""Écriture des géométries d'un dépôt SIG — délégué à persist_depot.

Ne filtre plus : toutes les entités lisibles sont persistées. Conservé comme
point d'entrée historique pour les appels internes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .modeles import ReferentielVerrouille, ZoneCandidate


def persister_geoms_referentiel(
    projet_id: str,
    ref: ReferentielVerrouille,
    sig_zip: str | Path,
    zones: list[ZoneCandidate] | list[dict],
    couches: list | None = None,
    depot_id: str | None = None,
) -> dict[str, Any]:
    from api.projets.geometries.persist_depot import persister_depot_sig

    return persister_depot_sig(
        projet_id=projet_id,
        ref=ref,
        sig_zip=sig_zip,
        zones=zones,
        couches=couches,
        depot_id=depot_id,
    )
