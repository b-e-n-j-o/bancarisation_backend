"""Normalisation des identifiants d'unités de gestion.

Convention : géométrie.ug_id ∈ *.ug_ids[]
  "UG 1" / "UG1" / "ug1" → "ug1"

Sentinel d'incertitude (claims / échéances / occurrences, jamais une géométrie) :
  "à définir" / "a_definir" / "inconnu" → "a_definir"
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")

# Valeur canonique quand le LLM (ou l'utilisateur) ne sait pas rattacher.
UG_INDEFINIE = "a_definir"

_SENTINELS_INDEFINI = frozenset(
    {
        "adefinir",
        "adefinie",
        "indefini",
        "indefinie",
        "inconnu",
        "inconnue",
        "indetermine",
        "indeterminee",
        "tbd",
        "unknown",
        "na",
        "none",
        "null",
    }
)


def _ascii_alnum(raw: str) -> str:
    """Minuscule, sans accents ni ponctuation : « À définir » → adefinir."""
    decomp = unicodedata.normalize("NFKD", raw.strip())
    sans_accents = "".join(c for c in decomp if not unicodedata.combining(c))
    return _NON_ALNUM.sub("", sans_accents).lower()


def normalize_ug_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    cleaned = _ascii_alnum(s)
    if not cleaned:
        return None
    if cleaned in _SENTINELS_INDEFINI:
        return UG_INDEFINIE
    return cleaned


def normalize_ug_ids(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if not values:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        n = normalize_ug_id(v)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def reconcilier_ug_ids(
    ug_ids: Iterable[str] | None,
    *,
    connus: set[str] | frozenset[str] | None = None,
    zone_proposee: str | None = None,
    alias_vers_ug: dict[str, str] | None = None,
    forcer_indefini_si_vide: bool = True,
) -> tuple[list[str], list[str]]:
    """Normalise + contraint aux UG connues du projet.

    Retourne ``(ug_ids, champs_a_confirmer_extra)``.
    - IDs hors référentiel SIG → retirés ; si plus rien → ``a_definir``.
    - ``zone_proposee`` / alias (libellé, nom fichier) peuvent sauver un match.
    - Sans SIG en amont (``connus`` vide/None) : on garde les ids normalisés
      tels quels (sauf sentinels), ou ``a_definir`` si vide.
    """
    extras: list[str] = []
    connus_n = {c for c in (normalize_ug_id(x) or "" for x in (connus or ())) if c}
    alias = {
        k.strip().lower(): v
        for k, v in (alias_vers_ug or {}).items()
        if k and v
    }

    out = normalize_ug_ids(list(ug_ids) if ug_ids else [])

    # Secours via zone proposée (nom fichier / libellé plan)
    if not out and zone_proposee:
        cle = zone_proposee.strip().lower()
        via = alias.get(cle)
        if not via:
            for k, v in alias.items():
                if len(k) < 4:
                    continue
                if k in cle or cle in k:
                    via = v
                    break
        if not via:
            via = normalize_ug_id(zone_proposee)
        if via and (not connus_n or via in connus_n or via == UG_INDEFINIE):
            out = [via]

    if connus_n:
        filtres = [u for u in out if u in connus_n or u == UG_INDEFINIE]
        if out and not filtres:
            extras.append("ug_ids")
        out = filtres

    if not out and forcer_indefini_si_vide:
        out = [UG_INDEFINIE]
        if "ug_ids" not in extras:
            extras.append("ug_ids")

    return out, extras


def formater_referentiel_ug_pour_prompt(
    zones: list,
    *,
    max_zones: int = 40,
) -> str:
    """Bloc texte injecté dans le prompt distributeur (mapping ug_id ↔ libellé)."""
    if not zones:
        return (
            "Aucune unité de gestion SIG n'est encore rattachée à ce projet.\n"
            'Renseigne `ug_ids: ["a_definir"]` sur chaque fiche et échéance.'
        )

    lignes = [
        "UNITÉS DE GESTION du projet (issues de l'import SIG — référentiel AUTORISÉ) :",
        "Pour chaque fiche/échéance, `ug_ids` DOIT contenir UNIQUEMENT des codes "
        'de cette liste (ex. "ug1"), éventuellement plusieurs si l\'action '
        'couvre plusieurs UG. Si tu n\'es pas confiant → ["a_definir"].',
        "N'invente JAMAIS un ug_id hors liste. Le libellé / nom de fichier sert "
        "à matcher le texte du plan (ex. « compensation fadet ») vers le code.",
        "",
    ]
    for z in zones[:max_zones]:
        ug = getattr(z, "ug_id", None) or "?"
        lib = getattr(z, "libelle", None) or getattr(z, "nom_source", "") or ""
        src = getattr(z, "source_fichier", None) or ""
        cat = getattr(z, "categorie", None) or ""
        surf = getattr(z, "surface_ha", None)
        bits = [f"{ug}"]
        if lib:
            bits.append(f"libellé « {lib} »")
        if src and src != lib:
            bits.append(f"fichier « {src} »")
        if cat:
            bits.append(cat)
        if surf is not None:
            try:
                bits.append(f"{float(surf):.1f} ha")
            except (TypeError, ValueError):
                pass
        lignes.append("- " + " · ".join(bits))

    reste = len(zones) - max_zones
    if reste > 0:
        lignes.append(f"…et {reste} autres")
    lignes.append("")
    lignes.append('Sentinel autorisé si doute : "a_definir".')
    return "\n".join(lignes)
