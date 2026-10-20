"""Insertion des grilles lues à leur place dans le texte OCR d'une page.

Principe : on n'enlève jamais d'information. Pour chaque grille (dans l'ordre vertical) :
1. on cherche dans le texte de la page la ligne qui correspond à `contexte.ancre_avant`
   (dernière ligne du PDF juste au-dessus de la grille), par recouvrement de mots ;
2. on cherche, à partir de là, le ou les tableaux Markdown de l'OCR qui portent les mêmes
   libellés temporels (années, N+k, mois) que la grille ; une frise sur deux rangées donne
   souvent deux tableaux OCR consécutifs, qui sont traités ensemble ;
3. décision :
   - `remplace`  : mode « remplacer », grille de confiance haute et tableau(x) OCR reconnu(s) →
                   le tableau OCR est remplacé par la grille (il est gardé dans insertions.json) ;
   - `accompagne`: tableau OCR trouvé mais remplacement non sûr → la grille est insérée juste
                   après le tableau OCR ;
   - `insere`    : pas de tableau OCR (texte natif, ou OCR qui a raté le tableau) → la grille
                   est insérée après la ligne d'ancrage ;
   - `insere_avant` : ancre du dessus introuvable → insertion avant la ligne qui suit la grille ;
   - `debut_de_page` : la grille est en haut de page sans texte au-dessus (suite de tableau) ;
   - `fin_de_page` : aucune ancre retrouvée → bloc en fin de page, marqué « position approximative ».
"""
from __future__ import annotations

import re

from ..m1_detection.geometrie import normaliser
from ..m1_detection.temporel import NOMS_MOIS

RE_MOT = re.compile(r"[A-Za-zÀ-ÿ0-9+]{3,}")
RE_TEMPOREL = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)|\bn\+\d{1,2}\b|\b(?:" +
                         "|".join(normaliser(m) for m in NOMS_MOIS[1:]) + r")\b")
SEUIL_ANCRE = 0.6
SEUIL_TABLEAU = 0.8


def _mots(t: str) -> set[str]:
    return {normaliser(m) for m in RE_MOT.findall(t or "")}


def _temporels(t: str) -> set[str]:
    return set(RE_TEMPOREL.findall(normaliser(t or "").replace(" ", " ")))


def jetons_grille(g: dict) -> set[str]:
    out = set()
    for c in g["colonnes"]:
        out |= _temporels(c["libelle"])
        if c.get("mois"):
            out.add(normaliser(NOMS_MOIS[c["mois"]]))
    return out


def trouver_ancre(lignes: list[str], ancre: str | None, debut: int = 0) -> int | None:
    """Indice de la ligne du texte qui correspond le mieux à l'ancre (recouvrement de mots)."""
    cible = _mots(ancre)
    if len(cible) < 2:
        return None
    meilleur, score_max = None, SEUIL_ANCRE
    for i in range(debut, len(lignes)):
        mots = _mots(lignes[i])
        if not mots:
            continue
        score = len(cible & mots) / len(cible)
        if score > score_max or (score == score_max and meilleur is None):
            meilleur, score_max = i, score
    return meilleur


def tableaux_markdown(lignes: list[str]) -> list[tuple[int, int]]:
    """Blocs de lignes consécutives commençant par « | » : [(debut, fin_exclue)]."""
    blocs, i = [], 0
    while i < len(lignes):
        if lignes[i].lstrip().startswith("|"):
            j = i
            while j < len(lignes) and lignes[j].lstrip().startswith("|"):
                j += 1
            blocs.append((i, j))
            i = j
        else:
            i += 1
    return blocs


def tableaux_correspondants(lignes: list[str], blocs: list[tuple[int, int]], jetons: set[str],
                            a_partir_de: int) -> list[tuple[int, int, float]]:
    """Tableaux OCR après l'ancre dont les libellés temporels appartiennent à la grille.

    Score = part des jetons temporels du tableau présents dans la grille (≥ 4 jetons exigés).
    On garde le premier tableau qui correspond et ceux qui le suivent immédiatement (rangées).
    """
    retenus: list[tuple[int, int, float]] = []
    for d, f in blocs:
        if f <= a_partir_de:
            continue
        tj = _temporels(" ".join(lignes[d:f]))
        score = len(tj & jetons) / len(tj) if len(tj) >= 4 else 0.0
        if score >= SEUIL_TABLEAU:
            if retenus and any(l.strip() and not l.lstrip().startswith("|") for l in lignes[retenus[-1][1]:d]):
                break  # du texte entre deux tableaux : ce n'est plus la même frise
            retenus.append((d, f, round(score, 2)))
        elif retenus:
            break
    return retenus


def inserer_grilles(texte: str, grilles: list[tuple[dict, str]], mode: str = "remplacer") -> tuple[str, list[dict], list[str]]:
    """Insère les blocs de grilles dans le texte d'une page.

    grilles : [(grille, bloc_markdown)] dans l'ordre vertical de la page.
    Renvoie (texte modifié, trace des décisions, blocs à placer en fin de page).
    """
    lignes = texte.split("\n")
    blocs_md = tableaux_markdown(lignes)
    operations = []          # (debut, fin_exclue_a_remplacer, bloc, trace)
    fin_de_page, trace = [], []
    curseur = 0
    for g, bloc in grilles:
        ctx = g.get("contexte") or {}
        jetons = jetons_grille(g)
        ancre = trouver_ancre(lignes, ctx.get("ancre_avant"), curseur)
        depart = ancre + 1 if ancre is not None else curseur
        tabs = tableaux_correspondants(lignes, blocs_md, jetons, depart)
        if tabs and ancre is not None and tabs[0][0] - ancre > 40:
            tabs = []  # tableau trop loin de l'ancre : ce n'est pas le bon
        if not tabs and ancre is None:
            tabs = tableaux_correspondants(lignes, blocs_md, jetons, curseur)  # pas d'ancre : le tableau seul suffit
        t = {"grille": g["id"], "ancre_trouvee": ancre is not None, "ancre": ctx.get("ancre_avant")}
        if tabs:
            d, f = tabs[0][0], tabs[-1][1]
            if mode == "remplacer" and g["confiance"] == "haute":
                t.update(decision="remplace", tableaux_ocr=[s for _, _, s in tabs],
                         texte_ocr_remplace="\n".join(lignes[d:f]))
                operations.append((d, f, bloc, t))
            else:
                t.update(decision="accompagne", tableaux_ocr=[s for _, _, s in tabs])
                operations.append((f, f, bloc, t))
            curseur = f
        elif ancre is not None:
            t.update(decision="insere")
            operations.append((ancre + 1, ancre + 1, bloc, t))
            curseur = ancre + 1
        elif (apres := trouver_ancre(lignes, ctx.get("ancre_apres"), curseur)) is not None:
            t.update(decision="insere_avant", ancre_apres=ctx.get("ancre_apres"))
            operations.append((apres, apres, bloc, t))
            curseur = apres
        elif not ctx.get("ancre_avant"):
            # rien au-dessus de la grille sur la page (suite de tableau) : début de page
            t.update(decision="debut_de_page")
            operations.append((curseur, curseur, bloc, t))
        else:
            t.update(decision="fin_de_page")
            fin_de_page.append(bloc.replace("]\n", "] (position approximative : fin de page)\n", 1))
        t["ligne"] = operations[-1][0] if operations and operations[-1][3] is t else None
        trace.append(t)

    # application de bas en haut pour ne pas décaler les indices
    for d, f, bloc, _ in sorted(operations, key=lambda o: (o[0], o[1]), reverse=True):
        lignes[d:f] = ["", bloc, ""]
    return "\n".join(lignes), trace, fin_de_page
