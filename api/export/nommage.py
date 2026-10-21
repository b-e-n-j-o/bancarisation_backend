"""Noms de fichiers ASCII, chemins courts, dédoublonnage."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TIRETS = re.compile(r"-{2,}")


def slug(texte: str | None, max_len: int = 60) -> str:
    """ASCII minuscule, séparateur « - », tronqué."""
    brut = unicodedata.normalize("NFKD", texte or "")
    ascii_txt = brut.encode("ascii", "ignore").decode("ascii").lower()
    propre = _TIRETS.sub("-", _NON_ALNUM.sub("-", ascii_txt)).strip("-")
    if not propre:
        propre = "sans-nom"
    if len(propre) > max_len:
        propre = propre[:max_len].rstrip("-")
    return propre or "sans-nom"


def racine_zip(reference: str | None, projet_id: str, nom: str, jour: str) -> str:
    ref = slug(reference, 24) if reference else projet_id.replace("-", "")[:8]
    return f"KerERC_{ref}_{slug(nom, 40)}_{jour}"


def _suffixer(chemin: str, n: int) -> str:
    path = Path(chemin)
    return str(path.with_name(f"{path.stem}_{n}{path.suffix}"))


def reserver(chemin: str, racine: str, pris: set[str], max_len: int = 180) -> str:
    """Garantie : len(racine/chemin) <= max_len, ASCII, unique dans le ZIP."""
    relatif = _ascii_chemin(chemin)
    relatif = _couper(relatif, racine, max_len)
    candidat = relatif
    n = 2
    while candidat in pris or len(f"{racine}/{candidat}") > max_len:
        candidat = _couper(_suffixer(relatif, n), racine, max_len)
        n += 1
        if n > 99:
            raise RuntimeError(f"Impossible de dédoublonner {chemin}")
    pris.add(candidat)
    return candidat


def _ascii_chemin(chemin: str) -> str:
    parts = []
    for morceau in chemin.replace("\\", "/").split("/"):
        if not morceau or morceau in {".", ".."}:
            continue
        stem = Path(morceau).stem
        suffixe = Path(morceau).suffix.lower()
        suffixe_ascii = "".join(ch for ch in suffixe if ch.isascii())
        parts.append(slug(stem, 60) + suffixe_ascii)
    return "/".join(parts) or "fichier"


def _couper(chemin: str, racine: str, max_len: int) -> str:
    budget = max_len - len(racine) - 1
    if budget < 12:
        budget = 12
    if len(chemin) <= budget:
        return chemin
    path = Path(chemin)
    suffixe = path.suffix
    dossier = str(path.parent)
    if dossier == ".":
        dossier = ""
    reste = budget - len(suffixe) - (len(dossier) + 1 if dossier else 0)
    stem = path.stem[: max(reste, 8)].rstrip("-")
    nom = f"{stem}{suffixe}"
    return f"{dossier}/{nom}" if dossier else nom
