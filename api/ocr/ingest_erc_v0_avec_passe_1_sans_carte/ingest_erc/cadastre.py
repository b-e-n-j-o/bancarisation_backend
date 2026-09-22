"""Normalisation tolérante des références cadastrales.

Les documents écrivent la même parcelle de 5 façons :
  SIG   : 33029C444a, 33029C1076_bis
  PDF   : 330290B1103, 3302940BZ153, 30290C480b (coquille), 33029444b (section manquante)
  Excel : colonnes commune / préfixe / section / numéro / subdivision
La clé canonique est  SECTION + NUMERO + subdiv  (ex. "C444a"), la commune à part.
"""
from __future__ import annotations

import re
from typing import Optional

RE_QUEUE = re.compile(r"(?:(?P<sec>[A-Z]{1,2})0*)?(?P<num>\d{1,4})(?P<sub>[a-z]?)(?:_?bis|_\d+)?$")
RE_REF_TEXTE = re.compile(r"\b\d{4,6}[0-9A-Z]{0,4}?[A-Z]{0,2}\d{1,4}[a-z]?\b")


def cle_parcelle(section: Optional[str], numero, subdiv: Optional[str] = "") -> str:
    sec = (section or "?").strip().upper() or "?"
    try:
        num = str(int(str(numero).strip()))
    except ValueError:
        num = str(numero).strip()
    return f"{sec}{num}{(subdiv or '').strip().lower()}"


def parse_ref(ref: str) -> Optional[dict]:
    """'330290C480b' -> {'commune':'33029','cle':'C480b','partielle':False}"""
    r = ref.strip().replace(" ", "")
    commune = r[:5] if r[:5].isdigit() else None
    corps = r[5:] if commune else r
    # le préfixe (0, 000, 40…) précède la section : on garde la dernière lettre-section
    m = RE_QUEUE.search(corps)
    if not m:
        return None
    sec = m.group("sec")
    if sec is None:
        # "33029444b" : pas de section -> clé partielle ; retirer un préfixe numérique évident
        num = m.group("num")
        return {"commune": commune, "cle": f"?{int(num)}{m.group('sub')}", "partielle": True,
                "brut": ref}
    return {"commune": commune, "cle": cle_parcelle(sec, m.group("num"), m.group("sub")),
            "partielle": False, "brut": ref}


def refs_dans_texte(txt: str) -> list[dict]:
    out = []
    for tok in re.findall(r"\b[0-9]{4,}[0-9A-Z]*[a-z]?\b", txt):
        if not re.search(r"[A-Z]", tok) and len(tok) < 7:
            continue  # simple nombre
        p = parse_ref(tok)
        if p and len(tok) >= 7:
            out.append(p)
    return out


def correspond(cle_a: str, cle_b: str) -> bool:
    if cle_a == cle_b:
        return True
    if cle_a.startswith("?"):
        return cle_b.endswith(cle_a[1:]) and re.match(r"[A-Z]+" + re.escape(cle_a[1:]) + "$", cle_b) is not None
    if cle_b.startswith("?"):
        return correspond(cle_b, cle_a)
    return False
