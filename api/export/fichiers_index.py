"""Index data/_fichiers.csv : relie chaque document à son chemin dans le ZIP."""

from __future__ import annotations

import csv

from api.export.contexte import ExportContexte, FichierExport
from api.export.manifest import empreinte
from api.export.modules._fichiers import fichier


def ecrire_index_fichiers(ctx: ExportContexte, fichiers: list[FichierExport]) -> FichierExport | None:
    if not ctx.alias_fichiers:
        return None
    par_chemin = {fic.chemin_archive: fic for fic in fichiers}
    lignes_csv = []
    for document_id, chemin in sorted(ctx.alias_fichiers.items()):
        fic = par_chemin.get(chemin)
        if fic is None or not fic.chemin_local.is_file():
            ctx.avertir(f"Index des fichiers : {chemin} introuvable pour le document {document_id}.")
            continue
        sha, _taille = empreinte(fic.chemin_local)
        lignes_csv.append({"document_id": document_id, "chemin_archive": chemin, "sha256": sha})
    if not lignes_csv:
        return None
    fic = fichier(
        ctx,
        "data/_fichiers.csv",
        "donnees",
        "Correspondance document → fichier du ZIP",
        len(lignes_csv),
    )
    with fic.chemin_local.open("w", encoding="utf-8", newline="") as sortie:
        writer = csv.DictWriter(sortie, fieldnames=["document_id", "chemin_archive", "sha256"])
        writer.writeheader()
        writer.writerows(lignes_csv)
    return fic
