"""
corpus.py — Le contrat de la couche 0 : tout document devient un CORPUS de BLOCS ADRESSABLES.

Principe : un extracteur ne voit jamais un PDF ni un XLSX, il voit du markdown
porteur d'ancres. Une ancre est une adresse stable et vérifiable dans le document
source ("plan-gestion-a1b2#p34", "estimation-c3d4#Planning!8:40").

Pourquoi c'est la première brique : sans ancre, aucune sortie LLM n'est
vérifiable par le chargé du BE, et rien de ce qui suit (réconciliation,
patch, audit de couverture) ne peut être tracé.

Format de l'ancre :  {doc_id}#{locator}
    PDF   → p34            (une page)         · p34-36 (plage)
    XLSX  → Planning!8:40  (feuille + lignes)
    DOCX  → §3             (bloc de section)
Le locator est OPAQUE pour les couches hautes : seul le normaliseur du format
sait le fabriquer, seule l'UI sait le résoudre en "page 34 du PDF".
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from pydantic import BaseModel, Field

# Marqueur injecté dans le markdown envoyé au LLM. Court = peu de tokens,
# inhabituel = pas de collision avec le contenu OCR.
MARQUEUR = "⟦{loc}⟧"
RE_MARQUEUR = re.compile(r"⟦([^⟧]+)⟧")


class TypeBloc(str, Enum):
    titre = "titre"
    texte = "texte"
    tableau = "tableau"
    meta = "meta"


class Bloc(BaseModel):
    """Unité adressable minimale. Grain choisi par format : page pour un PDF,
    table détectée pour une feuille Excel, section pour un DOCX."""

    locator: str
    type: TypeBloc = TypeBloc.texte
    texte: str
    titre: Optional[str] = Field(None, description="Titre de section couvrant ce bloc")
    meta: dict = Field(default_factory=dict)

    @property
    def vide(self) -> bool:
        return not self.texte.strip()


class DocumentNormalise(BaseModel):
    doc_id: str
    nom_fichier: str
    format: str  # pdf | xlsx | docx | md | txt
    sha256: str
    blocs: list[Bloc] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)

    # --- adressage ---------------------------------------------------------

    def ancre(self, locator: str) -> str:
        return f"{self.doc_id}#{locator}"

    def bloc(self, locator: str) -> Optional[Bloc]:
        return next((b for b in self.blocs if b.locator == locator), None)

    def locator_pour_ligne(self, feuille: str, ligne: int) -> Optional[str]:
        """XLSX : retrouve le bloc (table) qui contient une ligne Excel donnée.
        Sert à rattacher une LigneBudget extraite à son ancre."""
        for b in self.blocs:
            if b.meta.get("feuille") == feuille:
                d, f = b.meta.get("lignes", (0, -1))
                if d <= ligne <= f:
                    return b.locator
        return None

    # --- rendu pour le LLM -------------------------------------------------

    def rendu(
        self,
        locators: Optional[Iterable[str]] = None,
        avec_ancres: bool = True,
        max_car: Optional[int] = None,
    ) -> str:
        """Markdown du document (ou d'une sélection de blocs), ancres incluses.

        C'est CE texte que reçoivent les extracteurs — jamais le fichier brut.
        """
        cibles = list(locators) if locators else [b.locator for b in self.blocs]
        morceaux: list[str] = []
        for loc in cibles:
            b = self.bloc(loc)
            if b is None or b.vide:
                continue
            entete = MARQUEUR.format(loc=b.locator) if avec_ancres else ""
            morceaux.append(f"{entete}\n{b.texte.strip()}")
        texte = "\n\n".join(morceaux)
        return texte[:max_car] if max_car else texte

    def apercu(self, car_par_bloc: int = 240, max_blocs: int = 40) -> str:
        """Version courte pour le TRIAGE : de quoi reconnaître un rôle sans
        payer le document entier en tokens."""
        lignes = [f"### {self.doc_id} — {self.nom_fichier} ({self.format}, "
                  f"{len(self.blocs)} blocs)"]
        for b in self.blocs[:max_blocs]:
            extrait = " ".join(b.texte.split())[:car_par_bloc]
            titre = f" · {b.titre}" if b.titre else ""
            lignes.append(f"⟦{b.locator}⟧{titre} : {extrait}")
        if len(self.blocs) > max_blocs:
            lignes.append(f"… ({len(self.blocs) - max_blocs} blocs non montrés)")
        return "\n".join(lignes)


class Corpus(BaseModel):
    racine: str
    documents: list[DocumentNormalise] = Field(default_factory=list)

    def doc(self, doc_id: str) -> Optional[DocumentNormalise]:
        return next((d for d in self.documents if d.doc_id == doc_id), None)

    def apercu(self, car_par_bloc: int = 240) -> str:
        return "\n\n".join(d.apercu(car_par_bloc) for d in self.documents)

    def resoudre(self, ancre: str) -> Optional[tuple[DocumentNormalise, Bloc]]:
        if "#" not in ancre:
            return None
        doc_id, loc = ancre.split("#", 1)
        d = self.doc(doc_id)
        if d is None:
            return None
        b = d.bloc(loc)
        return (d, b) if b else None


# --- utilitaires ----------------------------------------------------------


def slug(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:40] or "doc"


def fabriquer_doc_id(chemin: Path, sha: str) -> str:
    """Stable entre deux runs (même fichier → même id) et unique (hash)."""
    return f"{slug(chemin.stem)}-{sha[:6]}"


def sha256_fichier(chemin: Path) -> str:
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for morceau in iter(lambda: f.read(1 << 20), b""):
            h.update(morceau)
    return h.hexdigest()