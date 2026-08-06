"""
texte.py — DOCX, markdown et texte plat → blocs de section.

Grain retenu : UNE SECTION PAR BLOC (découpe sur les titres), locator "§N".
Pas de page dans un DOCX : la section est la seule adresse stable qu'un humain
retrouve. On garde le chemin de titres dans meta pour l'affichage UI
("3.2 Modalités d'entretien").
"""

from __future__ import annotations

import re
from pathlib import Path

from .corpus import Bloc, DocumentNormalise, TypeBloc

RE_TITRE_MD = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
CAR_MAX_SECTION = 12000  # une section trop longue est recoupée pour rester prompt-able


def normaliser_texte(chemin: Path, doc_id: str, **_) -> DocumentNormalise:
    brut = chemin.read_text(encoding="utf-8", errors="replace")
    return DocumentNormalise(
        doc_id=doc_id,
        nom_fichier=chemin.name,
        format=chemin.suffix.lstrip(".").lower(),
        sha256="",
        blocs=_sections_markdown(brut),
    )


def normaliser_docx(chemin: Path, doc_id: str, **_) -> DocumentNormalise:
    from docx import Document as Docx

    doc = Docx(str(chemin))
    morceaux: list[str] = []
    for p in doc.paragraphs:
        texte = p.text.strip()
        if not texte:
            continue
        niveau = _niveau_titre(p.style.name if p.style else "")
        morceaux.append(("#" * niveau + " " + texte) if niveau else texte)

    for table in doc.tables:
        morceaux.append(_table_docx_markdown(table))

    return DocumentNormalise(
        doc_id=doc_id,
        nom_fichier=chemin.name,
        format="docx",
        sha256="",
        blocs=_sections_markdown("\n\n".join(morceaux)),
    )


# --- découpe ---------------------------------------------------------------


def _niveau_titre(style: str) -> int:
    m = re.search(r"(?:Heading|Titre)\s*(\d)", style or "")
    return int(m.group(1)) if m else 0


def _sections_markdown(brut: str) -> list[Bloc]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for ligne in brut.splitlines():
        m = RE_TITRE_MD.match(ligne)
        if m:
            sections.append((m.group(2).strip(), [ligne]))
        else:
            sections[-1][1].append(ligne)

    blocs: list[Bloc] = []
    for titre, lignes in sections:
        texte = "\n".join(lignes).strip()
        if not texte:
            continue
        for morceau in _recouper(texte):
            i = len(blocs) + 1
            blocs.append(
                Bloc(
                    locator=f"§{i}",
                    type=TypeBloc.tableau if morceau.lstrip().startswith("|") else TypeBloc.texte,
                    texte=morceau,
                    titre=titre,
                    meta={"titre": titre},
                )
            )
    return blocs


def _recouper(texte: str) -> list[str]:
    if len(texte) <= CAR_MAX_SECTION:
        return [texte]
    morceaux, courant = [], []
    taille = 0
    for para in texte.split("\n\n"):
        if taille + len(para) > CAR_MAX_SECTION and courant:
            morceaux.append("\n\n".join(courant))
            courant, taille = [], 0
        courant.append(para)
        taille += len(para)
    if courant:
        morceaux.append("\n\n".join(courant))
    return morceaux


def _table_docx_markdown(table) -> str:
    lignes = []
    for i, row in enumerate(table.rows):
        cellules = [c.text.replace("\n", " ").replace("|", "/").strip() for c in row.cells]
        lignes.append("| " + " | ".join(cellules) + " |")
        if i == 0:
            lignes.append("| " + " | ".join("---" for _ in cellules) + " |")
    return "\n".join(lignes)