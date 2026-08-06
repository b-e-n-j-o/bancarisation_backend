"""
pdf.py — PDF → blocs de page via Mistral OCR.

Grain retenu : UN BLOC PAR PAGE. C'est le grain que le chargé du BE sait
vérifier ("page 34"), et celui que l'OCR fournit nativement. Le découpage plus
fin (fiche-action) est du ressort de l'extracteur, pas du normaliseur : une
fiche peut chevaucher deux pages, une page peut porter deux fiches.

L'appel OCR est INJECTABLE (paramètre `ocr_pages`) : si tu as déjà une fonction
qui marche dans test_ocr.py, passe-la, ne réécris rien.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Optional

from .corpus import Bloc, DocumentNormalise, TypeBloc

MODELE_OCR = os.environ.get("MISTRAL_OCR_MODEL", "mistral-ocr-latest")
RE_TITRE = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", re.MULTILINE)


def ocr_mistral(chemin: Path) -> list[str]:
    """Retourne le markdown page par page — délègue au client prod (robuste)."""
    from ...ocr_mistral import pdf_vers_markdown

    import tempfile

    with tempfile.TemporaryDirectory(prefix="ocr_md_") as tmp:
        out = Path(tmp)
        _, n = pdf_vers_markdown(chemin.read_bytes(), chemin.name, out)
        pages: list[str] = []
        for i in range(1, n + 1):
            p = out / f"page_{i:02d}.md"
            pages.append(p.read_text(encoding="utf-8") if p.exists() else "")
        return pages


def normaliser_pdf(
    chemin: Path,
    doc_id: str,
    ocr_pages: Optional[Callable[[Path], list[str]]] = None,
    cache_dir: Optional[Path] = None,
    **_,
) -> DocumentNormalise:
    pages = _pages_avec_cache(chemin, doc_id, ocr_pages or ocr_mistral, cache_dir)

    blocs: list[Bloc] = []
    titre_courant: Optional[str] = None
    for i, markdown in enumerate(pages, start=1):
        titres = RE_TITRE.findall(markdown)
        if titres:
            titre_courant = titres[0].strip()
        blocs.append(
            Bloc(
                locator=f"p{i}",
                type=TypeBloc.tableau if _majoritairement_tableau(markdown) else TypeBloc.texte,
                texte=markdown,
                titre=titre_courant,
                meta={
                    "page": i,
                    "titres": [t.strip() for t in titres],
                    "contient_tableau": "|" in markdown,
                },
            )
        )

    return DocumentNormalise(
        doc_id=doc_id,
        nom_fichier=chemin.name,
        format="pdf",
        sha256="",
        blocs=blocs,
        meta={"nb_pages": len(pages), "modele_ocr": MODELE_OCR},
    )


def _majoritairement_tableau(markdown: str) -> bool:
    lignes = [l for l in markdown.splitlines() if l.strip()]
    if not lignes:
        return False
    return sum(1 for l in lignes if l.lstrip().startswith("|")) / len(lignes) > 0.5


def _pages_avec_cache(chemin, doc_id, fn, cache_dir: Optional[Path]) -> list[str]:
    """L'OCR est le poste le plus cher et le plus lent : il est mis en cache par
    doc_id (donc par contenu, cf. sha256). Re-jouer le pipeline d'analyse sur un
    dossier ne doit jamais re-payer l'OCR."""
    from ..journal import note_ocr

    if cache_dir is None:
        pages = fn(chemin)
        note_ocr(chemin.name, len(pages), cache=False)
        return pages

    cache_dir.mkdir(parents=True, exist_ok=True)
    fichier = cache_dir / f"{doc_id}.pages.md"
    sep = "\n\n<<<PAGE>>>\n\n"
    if fichier.exists():
        pages = fichier.read_text(encoding="utf-8").split(sep)
        note_ocr(chemin.name, len(pages), cache=True)
        return pages

    pages = fn(chemin)
    fichier.write_text(sep.join(pages), encoding="utf-8")
    note_ocr(chemin.name, len(pages), cache=False)
    return pages