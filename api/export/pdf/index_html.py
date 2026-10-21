"""LISEZMOI et index HTML, produits après les modules à partir du manifeste."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from api.export.contexte import ExportContexte, FichierExport
from api.export.modules._fichiers import fichier
from api.export.pdf.rendu import rendre_pdf


def ecrire_lisezmoi(
    ctx: ExportContexte,
    fichiers: list[FichierExport],
    *,
    projet_nom: str,
    organisation_nom: str,
    genere_le: str,
    demandeur: str | None,
) -> FichierExport:
    fic = fichier(ctx, "00_LISEZMOI.pdf", "lisezmoi", "Notice de l'export", len(fichiers) + 1, compresser=False)
    rendre_pdf(
        "lisezmoi.html.j2",
        {
            "titre": "Lisez-moi",
            "projet_nom": projet_nom,
            "organisation_nom": organisation_nom,
            "genere_le": genere_le,
            "demandeur": demandeur,
            "options": ctx.options,
            "fichiers": [
                {
                    "chemin": item.chemin_archive,
                    "description": item.description,
                    "nb_elements": item.nb_elements,
                }
                for item in fichiers
            ] + [{"chemin": "00_LISEZMOI.pdf", "description": "Cette notice", "nb_elements": None},
                 {"chemin": "index.html", "description": "Sommaire cliquable", "nb_elements": None},
                 {"chemin": "manifest.json", "description": "Empreintes SHA-256", "nb_elements": None}],
            "avertissements": list(ctx.avertissements),
        },
        fic.chemin_local,
    )
    return fic


def ecrire_index(
    ctx: ExportContexte,
    fichiers: list[FichierExport],
    *,
    projet_nom: str,
    organisation_nom: str,
    genere_le: str,
) -> FichierExport:
    fic = fichier(ctx, "index.html", "index", "Sommaire de l'export", len(fichiers))
    blocs = _blocs(fichiers)
    fic.chemin_local.write_text(
        page_index(projet_nom, organisation_nom, genere_le, blocs, ctx.avertissements),
        encoding="utf-8",
    )
    return fic


def page_index(projet_nom, organisation_nom, genere_le, blocs: dict[str, list[dict]], avertissements: list[str]) -> str:
    parties = [
        "<!DOCTYPE html><html lang=\"fr\"><head><meta charset=\"utf-8\">",
        f"<title>{escape(projet_nom)}</title>",
        "<style>body{font-family:Georgia,serif;color:#1c241c;margin:2rem auto;max-width:52rem;padding:0 1rem}",
        "a{color:#245c3a} h1{font-size:1.6rem} h2{font-size:1.1rem;margin-top:1.6rem}",
        "li{margin:0.25rem 0} .muted{color:#5c6b5c}</style></head><body>",
        f"<h1>{escape(projet_nom)}</h1>",
        f"<p class=\"muted\">{escape(organisation_nom)} · {escape(genere_le)}</p>",
        "<p><a href=\"00_LISEZMOI.pdf\">Lisez-moi</a> · <a href=\"manifest.json\">Manifeste</a></p>",
    ]
    for dossier, entrees in blocs.items():
        parties.append(f"<h2>{escape(dossier)}</h2><ul>")
        for entree in entrees:
            parties.append(
                f"<li><a href=\"{escape(entree['chemin'])}\">{escape(entree['nom'])}</a>"
                f" <span class=\"muted\">{escape(entree['description'])}</span></li>"
            )
        parties.append("</ul>")
    if avertissements:
        parties.append("<h2>Avertissements</h2><ul>")
        for message in avertissements:
            parties.append(f"<li>{escape(message)}</li>")
        parties.append("</ul>")
    parties.append("</body></html>")
    return "".join(parties)


def _blocs(fichiers: list[FichierExport]) -> dict[str, list[dict[str, str]]]:
    blocs: dict[str, list[dict[str, str]]] = {}
    for fic in fichiers:
        dossier = fic.chemin_archive.split("/", 1)[0]
        if dossier == fic.chemin_archive:
            dossier = "Racine"
        blocs.setdefault(dossier, []).append({
            "chemin": fic.chemin_archive,
            "nom": Path(fic.chemin_archive).name,
            "description": fic.description,
        })
    return blocs
