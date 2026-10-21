"""Dépôt d'un fichier produit dans le dossier temporaire de l'export."""

from __future__ import annotations

from pathlib import Path

from api.export.contexte import ExportContexte, FichierExport


def destination(ctx: ExportContexte, chemin_archive: str) -> Path:
    cible = ctx.dossier_tmp / chemin_archive
    cible.parent.mkdir(parents=True, exist_ok=True)
    return cible


def fichier(
    ctx: ExportContexte,
    chemin_archive: str,
    module: str,
    description: str,
    nb_elements: int | None = None,
    compresser: bool = True,
) -> FichierExport:
    return FichierExport(
        chemin_archive=chemin_archive,
        chemin_local=destination(ctx, chemin_archive),
        module=module,
        description=description,
        nb_elements=nb_elements,
        compresser=compresser,
    )
