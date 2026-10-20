"""Sérialisation des grilles lues en Markdown lisible par un LLM (et par un humain).

Deux rendus complémentaires :
- un tableau Markdown où chaque marque est explicite (X, ■ pour une cellule colorée,
  ◀ texte ▶ pour une bande fusionnée) ;
- une « lecture » en phrases, ligne par ligne, qui ne demande aucune interprétation
  géométrique au LLM : c'est elle qui transporte l'information de couleur.
"""
from __future__ import annotations

from .temporel import NOMS_MOIS

SYMBOLES = {
    "croix": "X",
    "remplissage": "■",
    "remplissage_minoritaire": "■",
    "remplissage_uniforme": "▒",
}


def _nom_colonne(col: dict, type_axe: str) -> str:
    if type_axe == "mois" and col.get("mois"):
        return NOMS_MOIS[col["mois"]]
    return col["libelle"]


def _borne(col: dict, cote: str) -> str:
    """« 2031 (N+6) » : année et rang relatif de début ou de fin d'une colonne, même plage."""
    a, n = col.get(f"annee_{cote}"), col.get(f"n_{cote}")
    rel = None if n is None else ("N" if n == 0 else f"N+{n}")
    if a is not None and rel:
        return f"{a} ({rel})"
    return str(a) if a is not None else (rel or col["libelle"])


def _etendue(debut: dict, fin: dict, type_axe: str) -> str:
    if type_axe == "mois":
        return f"de {_nom_colonne(debut, type_axe)} à {_nom_colonne(fin, type_axe)}"
    return f"de {_borne(debut, 'debut')} à {_borne(fin, 'fin')}"


def _liste(noms: list[str]) -> str:
    if len(noms) <= 1:
        return "".join(noms)
    return ", ".join(noms[:-1]) + " et " + noms[-1]


def _libelle_defaut(grille: dict) -> str:
    ctx = grille.get("contexte") or {}
    if ctx.get("code_proche"):
        return f"(sans libellé — fiche {ctx['code_proche']} probable)"
    return "(sans libellé)"


def lecture_ligne(ligne: dict, grille: dict) -> str | None:
    cols, type_axe = grille["colonnes"], grille["type_axe"]
    morceaux = []
    par_type: dict[str, list] = {}
    for m in ligne["marques"]:
        par_type.setdefault(m["type"], []).append(m)
    if "croix" in par_type:
        noms = [_nom_colonne(cols[i], type_axe) for m in par_type["croix"] for i in m["colonnes"]]
        morceaux.append(f"croix en {_liste(noms)}")
    for t in ("remplissage", "remplissage_minoritaire"):
        if t in par_type:
            noms = [_nom_colonne(cols[i], type_axe) for m in par_type[t] for i in m["colonnes"]]
            teintes = sorted({m["couleur"] for m in par_type[t] if m["couleur"]})
            morceaux.append(f"cellules colorées ({', '.join(teintes)}) en {_liste(noms)}")
    for m in par_type.get("bande", []):
        debut, fin = cols[m["colonnes"][0]], cols[m["colonnes"][-1]]
        morceaux.append(f"bande « {m['texte']} » {_etendue(debut, fin, type_axe)}")
    for m in par_type.get("texte", []):
        morceaux.append(f"texte « {m['texte']} » en {_nom_colonne(cols[m['colonnes'][0]], type_axe)}")
    if "remplissage_uniforme" in par_type:
        morceaux.append("toute la ligne est colorée sans texte (marque ambiguë : à vérifier)")
    if not morceaux:
        return None
    libelle = ligne["libelle"] or _libelle_defaut(grille)
    role = " (cellules d'en-tête surlignées)" if ligne["role"] == "entete" else ""
    return f"- « {libelle} »{role} : " + " ; ".join(morceaux) + "."


def grille_en_markdown(grille: dict) -> str:
    cols, type_axe = grille["colonnes"], grille["type_axe"]
    entetes = [_nom_colonne(c, type_axe) for c in cols]
    lignes_md = ["| Ligne | " + " | ".join(entetes) + " |",
                 "|---|" + "---|" * len(cols)]
    for ligne in grille["lignes"]:
        cellules = [""] * len(cols)
        for m in ligne["marques"]:
            idx = m["colonnes"]
            if m["type"] in ("bande",):
                cellules[idx[0]] = f"◀ {m['texte']}"
                for i in idx[1:-1]:
                    cellules[i] = "↔"
                if len(idx) > 1:
                    cellules[idx[-1]] = "▶"
            elif m["type"] == "texte":
                cellules[idx[0]] = m["texte"]
            else:
                for i in idx:
                    cellules[i] = SYMBOLES.get(m["type"], "?")
        libelle = (ligne["libelle"] or _libelle_defaut(grille)).replace("|", "/")
        if ligne["role"] == "entete":
            libelle += " [en-tête]"
        lignes_md.append(f"| {libelle} | " + " | ".join(c.replace("|", "/") for c in cellules) + " |")

    lecture = [l for l in (lecture_ligne(li, grille) for li in grille["lignes"]) if l]
    titre = (f"[GRILLE {grille['id']} — page {grille['page']} — axe "
             f"{'mensuel' if type_axe == 'mois' else 'annuel'} — lecture géométrique du PDF "
             f"(sans OCR), confiance {grille['confiance']}]")
    ctx = grille.get("contexte") or {}
    infos = []
    if ctx.get("code_proche"):
        infos.append(f"Rattachement probable : code « {ctx['code_proche']} ».")
    if grille.get("fusion_de"):
        infos.append(f"Frise dessinée sur {len(grille['fusion_de'])} rangées dans le PDF, recollées en une seule grille.")
    corps = [titre, *infos, "", *lignes_md, "", "Lecture :", *(lecture or ["- aucune marque détectée."])]
    return "\n".join(corps)
