"""Journal d'étapes du pipeline multidocs (console + recap).

Conserve les logs streaming Mistral existants ; ajoute un cadre clair par
étape et un tableau récapitulatif en fin de run.
"""

from __future__ import annotations

import contextvars
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

_JOURNAL: contextvars.ContextVar[Optional["JournalPipeline"]] = contextvars.ContextVar(
    "multidocs_journal", default=None,
)


@dataclass
class LigneEtape:
    n: int
    nom: str
    script: str
    appels_llm: int = 0
    appels_ocr: int = 0
    detail_appels: str = ""
    resultat: str = ""
    ok: bool = True


@dataclass
class JournalPipeline:
    projet_id: str
    lignes: list[LigneEtape] = field(default_factory=list)
    ocr_appels: int = 0
    ocr_pages: int = 0
    ocr_cache_hits: int = 0
    _courante: Optional[LigneEtape] = field(default=None, repr=False)
    _ocr_au_debut_etape: int = field(default=0, repr=False)

    def activer(self) -> None:
        _JOURNAL.set(self)

    def debut(self, n: int, nom: str, script: str) -> None:
        self._courante = LigneEtape(n=n, nom=nom, script=script)
        self._ocr_au_debut_etape = self.ocr_appels
        barre = "─" * 62
        print(f"\n{barre}", flush=True)
        print(f"  ÉTAPE {n} · {nom}", flush=True)
        print(f"  {script}", flush=True)
        print(f"{barre}", flush=True)

    def fin(
        self,
        resultat: str,
        *,
        appels_llm: int = 0,
        detail_appels: str = "",
        ok: bool = True,
    ) -> None:
        if self._courante is None:
            return
        self._courante.resultat = resultat
        self._courante.appels_llm = appels_llm
        self._courante.appels_ocr = self.ocr_appels - self._ocr_au_debut_etape
        self._courante.detail_appels = detail_appels
        self._courante.ok = ok
        self.lignes.append(self._courante)
        marque = "✓" if ok else "✗"
        ocr = self._courante.appels_ocr
        llm = appels_llm
        print(
            f"\n  {marque} Fin étape {self._courante.n} · "
            f"OCR={ocr} · LLM={llm}"
            + (f" ({detail_appels})" if detail_appels else "")
            + f" · {resultat}",
            flush=True,
        )
        self._courante = None

    def abandonner(self, erreur: str) -> None:
        """Clôture l'étape en cours en erreur (si une est ouverte)."""
        if self._courante is not None:
            self.fin(erreur[:160], ok=False)

    def note_ocr(self, fichier: str, pages: int, *, cache: bool) -> None:
        if cache:
            self.ocr_cache_hits += 1
            print(
                f"📄 [OCR cache] {fichier} → {pages} page(s) (sans appel API)",
                flush=True,
            )
        else:
            self.ocr_appels += 1
            self.ocr_pages += pages
            print(
                f"📄 [OCR] {fichier} → {pages} page(s) (appel Mistral OCR)",
                flush=True,
            )

    def rapport(self) -> str:
        """Tableau texte prêt pour print / fichier."""
        cols = ("Étape", "Script", "Appels", "Résultat")
        rows: list[tuple[str, str, str, str]] = []
        for L in self.lignes:
            appels: list[str] = []
            if L.appels_ocr:
                appels.append(f"{L.appels_ocr} OCR")
            if L.appels_llm:
                detail = f" ({L.detail_appels})" if L.detail_appels else ""
                appels.append(f"{L.appels_llm} LLM{detail}")
            if not appels:
                appels.append("0")
            rows.append((
                f"{L.n}. {L.nom}",
                L.script,
                " · ".join(appels),
                ("✗ " if not L.ok else "") + L.resultat,
            ))

        w = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c)
             for i, c in enumerate(cols)]
        # plafonner script / résultat pour la console
        w[1] = min(w[1], 42)
        w[3] = min(w[3], 48)

        def trunc(s: str, n: int) -> str:
            return s if len(s) <= n else s[: n - 1] + "…"

        def fmt(row: tuple[str, str, str, str]) -> str:
            return (
                f"  {trunc(row[0], w[0]):<{w[0]}}  "
                f"{trunc(row[1], w[1]):<{w[1]}}  "
                f"{trunc(row[2], w[2]):<{w[2]}}  "
                f"{trunc(row[3], w[3])}"
            )

        sep = "  " + "─" * (sum(w) + 6)
        lignes = [
            "",
            sep,
            "  RÉCAP PIPELINE MULTIDOCS",
            sep,
            fmt(cols),
            sep,
        ]
        lignes.extend(fmt(r) for r in rows)
        total_llm = sum(L.appels_llm for L in self.lignes)
        total_ocr = sum(L.appels_ocr for L in self.lignes)
        lignes.append(sep)
        lignes.append(
            f"  Total OCR API : {total_ocr}  ·  cache hits : {self.ocr_cache_hits}  "
            f"·  pages OCR : {self.ocr_pages}  ·  Total LLM chat : {total_llm}"
        )
        lignes.append(sep)
        return "\n".join(lignes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projet_id": self.projet_id,
            "etapes": [asdict(L) for L in self.lignes],
            "ocr_appels": self.ocr_appels,
            "ocr_pages": self.ocr_pages,
            "ocr_cache_hits": self.ocr_cache_hits,
            "llm_appels": sum(L.appels_llm for L in self.lignes),
        }


def journal_actif() -> Optional[JournalPipeline]:
    return _JOURNAL.get()


def note_ocr(fichier: str, pages: int, *, cache: bool = False) -> None:
    j = journal_actif()
    if j is not None:
        j.note_ocr(fichier, pages, cache=cache)
    elif cache:
        print(f"📄 [OCR cache] {fichier} → {pages} page(s)", flush=True)
    else:
        print(f"📄 [OCR] {fichier} → {pages} page(s)", flush=True)
