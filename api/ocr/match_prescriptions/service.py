"""Orchestration métier : LLM d'appariement + persistance des propositions."""

from __future__ import annotations

import logging
from uuid import UUID

from api.ocr.match_prescriptions import crud
from api.ocr.match_prescriptions.match_prescriptions import (
    match_prescriptions,
    to_couverture_rows,
)
from api.ocr.match_prescriptions.schemas import ApparierResultOut

logger = logging.getLogger("match_prescriptions.service")


def lancer_appariement(
    projet_id: UUID,
    arrete_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> ApparierResultOut:
    presc, ech = crud.charger_pour_match(
        projet_id,
        arrete_id,
        role=role,
        organisation_id=organisation_id,
    )
    if not presc:
        raise ValueError("Aucune prescription sur cet arrêté — lancez d'abord l'extraction.")
    if not ech:
        raise ValueError(
            "Aucune échéance sur ce projet — importez d'abord le plan de gestion BE."
        )

    print(
        f"[appariement] démarrage projet={projet_id} arrete={arrete_id} "
        f"prescriptions={len(presc)} echeances={len(ech)}",
        flush=True,
    )
    liens, usage = match_prescriptions(presc, ech)
    rows = to_couverture_rows(liens)
    n, etat = crud.persister_propositions_ia(
        projet_id,
        arrete_id,
        rows,
        role=role,
        organisation_id=organisation_id,
    )
    print(
        f"[appariement] terminé — liens LLM={len(liens)} inseres={n} usage={usage}",
        flush=True,
    )
    logger.info("appariement OK | inseres=%s | %s", n, usage)
    return ApparierResultOut(liens_inseres=n, usage=usage, etat=etat)
