"""
etape1_normalisation — fichier hétérogène → DocumentNormalise.

Un seul point d'entrée : normaliser(chemin). Ajouter un format = ajouter un
module dans utils/ et une entrée dans NORMALISEURS. Rien d'autre dans la chaîne
ne connaît les formats.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .utils.corpus import Corpus, DocumentNormalise, fabriquer_doc_id, sha256_fichier
from .utils.pdf import normaliser_pdf
from .utils.sig import normaliser_sig, zip_contient_shapefile
from .utils.tableur import normaliser_xlsx
from .utils.texte import normaliser_docx, normaliser_texte

NORMALISEURS: dict[str, Callable] = {
    ".pdf": normaliser_pdf,
    ".xlsx": normaliser_xlsx,
    ".xlsm": normaliser_xlsx,
    ".docx": normaliser_docx,
    ".md": normaliser_texte,
    ".txt": normaliser_texte,
    ".csv": normaliser_texte,
    ".gpkg": normaliser_sig,
    ".geopackage": normaliser_sig,
    ".geojson": normaliser_sig,
    ".json": normaliser_sig,
    ".kml": normaliser_sig,
    ".shp": normaliser_sig,
    ".zip": normaliser_sig,  # reniflé : sans .shp → contenu documentaire
}

FORMATS_SUPPORTES = tuple(NORMALISEURS)


def normaliser(chemin: Path, **kw) -> DocumentNormalise:
    ext = chemin.suffix.lower()
    if ext == ".zip" and not zip_contient_shapefile(chemin):
        raise ValueError(
            f"ZIP sans shapefile : {chemin.name} — à dézipper et router vers "
            "la normalisation documentaire"
        )
    fn = NORMALISEURS.get(ext)
    if fn is None:
        raise ValueError(f"Format non pris en charge : {ext} ({chemin.name})")

    sha = sha256_fichier(chemin)
    doc_id = fabriquer_doc_id(chemin, sha)
    doc = fn(chemin, doc_id=doc_id, **kw)
    doc.sha256 = sha
    return doc


def normaliser_dossier(
    racine: Path,
    ignorer: Optional[set[str]] = None,
    **kw,
) -> tuple[Corpus, list[str]]:
    """Normalise tous les fichiers supportés d'un dossier déposé par le BE.

    Retourne (corpus, ignorés). Les ignorés sont remontés à l'UI : un .zip de
    shapefiles ou un .dwg non traité doit être VISIBLE, pas silencieux — c'est
    déjà un trou de couverture.
    """
    ignorer = ignorer or set()
    docs: list[DocumentNormalise] = []
    laisses: list[str] = []

    for chemin in sorted(p for p in racine.rglob("*") if p.is_file()):
        # Ignore fichiers cachés / AppleDouble (._…) — déjà couverts par startswith(".")
        if chemin.name.startswith(".") or chemin.name in ignorer:
            continue
        ext = chemin.suffix.lower()  # .PDF → .pdf
        if ext not in NORMALISEURS:
            laisses.append(f"{chemin.relative_to(racine)} (format non traité)")
            continue
        try:
            docs.append(normaliser(chemin, **kw))
        except Exception as err:  # noqa: BLE001 — on ne casse pas le dossier
            laisses.append(f"{chemin.relative_to(racine)} (échec : {err})")

    return Corpus(racine=str(racine), documents=docs), laisses