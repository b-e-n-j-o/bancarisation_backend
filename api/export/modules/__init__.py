"""Registre ordonné des modules d'export. Un échec n'arrête pas les suivants."""

from api.export.modules import (
    bilans,
    budget,
    calendrier,
    contacts,
    donnees,
    documents,
    echanges,
    foncier,
    geometries,
    obligations,
    projet,
)

MODULES = [
    projet,
    calendrier,
    budget,
    geometries,
    documents,
    contacts,
    foncier,
    obligations,
    echanges,
    bilans,
    donnees,
]
