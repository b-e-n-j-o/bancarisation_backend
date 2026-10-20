"""Détection et lecture géométrique des grilles temporelles (mois ou années).

Principe (aucun appel API) :
1. graines   : lignes de texte portant >= 6 mois distincts, la suite de lettres JFMAMJJASOND,
               ou >= 4 jetons année / N+k, espacés comme des colonnes (pas comme de la prose).
2. axe       : graines voisines regroupées (ex. ligne "N, N+1…" + ligne "2025, 2026…").
3. colonnes  : bornées par les traits verticaux qui entourent chaque libellé (cellule
               englobante) ; à défaut, milieux entre libellés voisins (confiance moyenne).
4. lignes    : intervalles entre traits horizontaux sous l'en-tête, jusqu'à la première
               ligne qui n'appartient plus à la grille (texte pleine largeur sans couleur).
5. cellules  : pour chaque (ligne, colonne), cellule englobante => cellules fusionnées
               (bandes), aplat de couleur, texte (croix, libellé de bande).
6. marques   : croix (texte X, ✓, ●…), remplissage (couleur ≠ blanc quand la ligne mélange
               blanc et couleur, ou couleur minoritaire), bande (cellule fusionnée avec texte).
               L'en-tête lui-même est testé : les fiches surlignent souvent directement les mois.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from .geometrie import GeometriePage, Mot, Rect, couleur_hex, texte_mots
from .temporel import LETTRES_MOIS, analyser_libelle, est_libelle_temporel, jeton_temporel

RE_CROIX = re.compile(r"^\s*(x|X|✓|✔|●|•|■|◼|★|\*|oui|OUI|Oui)\s*$")
CONNECTEURS = {"à", "a", "-", "–", "au", "/", "et"}
ECART_MIN_COLONNES = 5.0      # pt : écart médian minimal entre libellés (sinon c'est de la prose)
HAUTEUR_MAX_LIGNE = 160.0     # pt : au-delà, on considère que la grille est finie
ECART_MAX_GRAINES = 25.0      # pt : deux graines plus éloignées forment deux axes distincts


@dataclass
class Graine:
    type_axe: str            # "mois" | "annees"
    mots: list[Mot]          # mots temporels, ordonnés en x
    valeurs: list[int]       # mois (1-12) ou valeur du jeton
    y0: float
    y1: float

    @property
    def x0(self) -> float:
        return min(m.x0 for m in self.mots)

    @property
    def x1(self) -> float:
        return max(m.x1 for m in self.mots)


@dataclass
class Colonne:
    i: int
    x0: float
    x1: float
    bordee: bool
    mot_ref: Mot
    libelle: str = ""
    info: dict = field(default_factory=dict)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def largeur(self) -> float:
        return self.x1 - self.x0


# ---------------------------------------------------------------------------- graines
def _ecart_median(mots: list[Mot]) -> float:
    mots = sorted(mots, key=lambda m: m.x0)
    ecarts = [b.x0 - a.x1 for a, b in zip(mots, mots[1:])]
    return statistics.median(ecarts) if ecarts else 0.0


def detecter_graines(geo: GeometriePage) -> list[Graine]:
    graines: list[Graine] = []
    for ligne in geo.lignes_de_mots():
        jetons = [(m, jeton_temporel(m.texte)) for m in ligne]
        utiles = [m for m, j in jetons if j] + [m for m in ligne if m.texte in CONNECTEURS]
        ratio = len(utiles) / len(ligne)
        # Dans une grille, les mots voisins sont dans des cellules distinctes : écarts larges.
        # Dans la prose ("de N à N+5, puis à N+10…"), l'écart médian est celui d'une espace.
        en_colonnes = _ecart_median(ligne) >= ECART_MIN_COLONNES

        # Mois en toutes lettres ou abrégés
        mois = [(m, j[1]) for m, j in jetons if j and j[0] == "mois"]
        if len({v for _, v in mois}) >= 6 and ratio >= 0.5 and en_colonnes:
            vals = [v for _, v in mois]
            croissants = sum(1 for a, b in zip(vals, vals[1:]) if b > a)
            if croissants >= 0.8 * (len(vals) - 1):
                mots = [m for m, _ in mois]
                graines.append(Graine("mois", mots, vals, min(m.y0 for m in mots), max(m.y1 for m in mots)))
                continue

        # Initiales J F M A M J J A S O N D
        lettres = [m for m in ligne if len(m.texte) == 1 and m.texte.isalpha() and m.texte.isupper()]
        suite = "".join(m.texte for m in lettres)
        pos = suite.find(LETTRES_MOIS)
        if pos >= 0 and _ecart_median(lettres[pos:pos + 12]) >= ECART_MIN_COLONNES - 2:
            mots = lettres[pos:pos + 12]
            graines.append(Graine("mois", mots, list(range(1, 13)),
                                  min(m.y0 for m in mots), max(m.y1 for m in mots)))
            continue

        # Années absolues ou relatives
        ans = [(m, j) for m, j in jetons if j and j[0] in ("annee", "nrel")]
        if len(ans) >= 4 and len({j for _, j in ans}) >= 4 and ratio >= 0.4 and en_colonnes:
            mots = [m for m, _ in ans]
            graines.append(Graine("annees", mots, [j[1] for _, j in ans],
                                  min(m.y0 for m in mots), max(m.y1 for m in mots)))
    return graines


def grouper_graines(graines: list[Graine]) -> list[list[Graine]]:
    """Regroupe les graines du même type, empilées verticalement et alignées en x."""
    groupes: list[list[Graine]] = []
    for g in sorted(graines, key=lambda g: g.y0):
        for groupe in groupes:
            der = groupe[-1]
            rec = min(der.x1, g.x1) - max(der.x0, g.x0)
            if der.type_axe == g.type_axe and 0 <= g.y0 - der.y1 <= ECART_MAX_GRAINES \
                    and rec >= 0.5 * min(der.x1 - der.x0, g.x1 - g.x0):
                groupe.append(g)
                break
        else:
            groupes.append([g])
    return groupes


# ---------------------------------------------------------------------------- colonnes
def construire_colonnes(geo: GeometriePage, groupe: list[Graine]) -> tuple[list[Colonne], dict]:
    principale = max(groupe, key=lambda g: len(g.mots))
    mots_p = sorted(principale.mots, key=lambda m: m.cx)
    centres = [m.cx for m in mots_p]
    pas = statistics.median([b - a for a, b in zip(centres, centres[1:])]) if len(centres) > 1 else 40.0

    type_axe = groupe[0].type_axe

    # Bandeau d'en-tête : du haut de la cellule la plus haute au bas de la plus basse
    y0s, y1s = [], []
    for g in groupe:
        for m in g.mots:
            c = geo.cellule_englobante(m.cx, m.cy)
            y0s.append(c.y0 if c.bordee["h"] and m.y0 - c.y0 < 40 else m.y0 - 2)
            y1s.append(c.y1 if c.bordee["b"] and c.y1 - m.y1 < 40 else m.y1 + 2)
    bandeau = {"y0": min(y0s), "y1": max(y1s)}

    # Tous les mots temporels du bandeau, pas seulement ceux de la ligne-graine :
    # libellés sur plusieurs lignes ("N+16 à / N+20"), mots coupés ("Mar / s")…
    gx0 = min(g.x0 for g in groupe) - pas
    gx1 = max(g.x1 for g in groupe) + pas
    types_ok = {"mois"} if type_axe == "mois" else {"annee", "nrel"}
    mots_bandeau = [m for m in geo.mots_dans(gx0, bandeau["y0"], gx1, bandeau["y1"])
                    if (j := jeton_temporel(m.texte)) and j[0] in types_ok]
    mots_bandeau += [m for g in groupe for m in g.mots if m not in mots_bandeau]

    candidats: list[Colonne] = []
    for m in mots_bandeau:
        c = geo.cellule_englobante(m.cx, m.cy)
        ok = c.bordee["g"] and c.bordee["d"] and (c.x1 - c.x0) <= 4.2 * pas and (c.x1 - c.x0) >= m.x1 - m.x0
        if ok:
            candidats.append(Colonne(0, c.x0, c.x1, True, m))
    # Colonnes manquantes ou absence de bordures : milieux entre libellés de la graine principale
    fallback: list[Colonne] = []
    for k, m in enumerate(mots_p):
        g = (centres[k - 1] + m.cx) / 2 if k > 0 else m.cx - pas / 2
        d = (centres[k + 1] + m.cx) / 2 if k + 1 < len(mots_p) else m.cx + pas / 2
        fallback.append(Colonne(0, g, d, False, m))

    def dedoublonner(cols: list[Colonne]) -> list[Colonne]:
        sortie: list[Colonne] = []
        for c in sorted(cols, key=lambda c: (c.x0, c.x1)):
            if sortie and abs(sortie[-1].x0 - c.x0) < 1.5 and abs(sortie[-1].x1 - c.x1) < 1.5:
                continue
            sortie.append(c)
        return sortie

    bordees = dedoublonner(candidats)
    # Chaque mot de la graine principale doit tomber dans une colonne bordée, sans chevauchement
    couvre = all(any(c.x0 - 0.5 <= m.cx <= c.x1 + 0.5 for c in bordees) for m in mots_p)
    chevauche = any(b.x0 < a.x1 - 0.3 * min(a.largeur, b.largeur) for a, b in zip(bordees, bordees[1:]))
    if bordees and couvre and not chevauche:
        colonnes, mode = bordees, "bordures"
    else:
        colonnes, mode = dedoublonner(fallback), "milieux"

    for c in colonnes:
        mots = geo.mots_dans(c.x0, bandeau["y0"], c.x1, bandeau["y1"])
        c.libelle = texte_mots(mots)
        if type_axe == "mois" and principale.valeurs and c.mot_ref in principale.mots and len(c.mot_ref.texte) == 1:
            # initiales : le mois vient de la position dans la suite JFMAMJJASOND
            c.info = {"libelle": c.libelle, "mois": principale.valeurs[principale.mots.index(c.mot_ref)]}
        else:
            c.info = analyser_libelle(c.libelle, type_axe)
    colonnes = [c for c in colonnes if est_libelle_temporel(c.info, type_axe)]

    # Colonne « État zéro / État initial / T0 » collée à gauche de la première colonne : elle ne
    # contient aucun jeton temporel, donc aucune graine ne la crée ; on la rattache si elle est bordée.
    if type_axe == "annees" and colonnes and mode == "bordures":
        ym = (bandeau["y0"] + bandeau["y1"]) / 2
        cel = geo.cellule_englobante(colonnes[0].x0 - 2, ym)
        if cel.bordee["g"] and cel.bordee["d"] and abs(cel.x1 - colonnes[0].x0) < 2 \
                and cel.x1 - cel.x0 <= 4.2 * pas:
            lib = texte_mots(geo.mots_dans(cel.x0, bandeau["y0"], cel.x1, bandeau["y1"]))
            info = analyser_libelle(lib, type_axe)
            if info.get("etat_zero"):
                ref = Mot(cel.x0, bandeau["y0"], cel.x1, bandeau["y1"], lib)
                colonnes.insert(0, Colonne(0, cel.x0, cel.x1, True, ref, lib, info))
    for i, c in enumerate(colonnes):
        c.i = i
    return colonnes, {"mode_colonnes": mode, "bandeau": bandeau, "pas": pas}


# ---------------------------------------------------------------------------- marques
def regle_remplissage(couleurs: list[tuple | None], poids: list[int]) -> tuple[set[int], str]:
    """Indices marqués par la couleur, et mode de décision.

    - blanc et couleur mélangés : les cellules colorées sont marquées ;
    - tout coloré, plusieurs couleurs : les couleurs minoritaires sont marquées ;
    - tout coloré d'une seule couleur, ou tout blanc : la couleur ne porte pas d'information.
    """
    blancs = [i for i, c in enumerate(couleurs) if c is None]
    colores = [i for i, c in enumerate(couleurs) if c is not None]
    if blancs and colores:
        return set(colores), "remplissage"
    if colores:
        compte: Counter = Counter()
        for i in colores:
            compte[couleur_hex(couleurs[i])] += poids[i]
        if len(compte) > 1:
            majoritaire = compte.most_common(1)[0][0]
            return {i for i in colores if couleur_hex(couleurs[i]) != majoritaire}, "remplissage_minoritaire"
        return set(), "uniforme"
    return set(), "blanc"


def libelle_gauche(geo: GeometriePage, gauche_x0: float, zx0: float, a: float, b: float) -> str:
    """Libellé d'une ligne : texte des cellules situées à gauche de la zone temporelle et qui
    recoupent la ligne. Une cellule fusionnée verticalement (code « RG1 » sur trois sous-lignes)
    est rattachée à toutes les lignes qu'elle couvre. Cellules lues de gauche à droite."""
    bornes = Rect(gauche_x0 - 1, geo.rect.y0, zx0 + 1, geo.rect.y1)
    par_cellule: dict[tuple, list] = {}
    for m in geo.mots:
        if not (gauche_x0 - 1 <= m.cx < zx0 - 0.5):
            continue
        cel = geo.cellule_englobante(m.cx, m.cy, bornes)
        if cel.bordee["h"] and cel.bordee["b"] and cel.y1 - cel.y0 < 400:
            rec = min(cel.y1, b) - max(cel.y0, a)
            garder = rec >= 0.5 * min(b - a, cel.y1 - cel.y0)
        else:
            garder = a - 0.5 <= m.cy <= b + 0.5
        if garder:
            par_cellule.setdefault((round(cel.x0), round(cel.y0)), []).append(m)
    morceaux = [texte_mots(ms) for _, ms in sorted(par_cellule.items())]
    return " | ".join(t for t in morceaux if t)


def lire_entete(geo: GeometriePage, groupe: list[Graine], colonnes: list[Colonne], gauche_x0: float) -> list[dict]:
    """Marques portées par les cellules d'en-tête elles-mêmes (mois surlignés)."""
    sortie = []
    for g in groupe:
        couleurs = []
        for c in colonnes:
            mots = [m for m in g.mots if c.x0 - 0.5 <= m.cx <= c.x1 + 0.5]
            y = mots[0].cy if mots else (g.y0 + g.y1) / 2
            couleurs.append(geo.couleur_au_point(c.cx, y))
        marques, mode = regle_remplissage(couleurs, [1] * len(colonnes))
        if not marques:
            continue
        libelle = libelle_gauche(geo, gauche_x0, colonnes[0].x0, g.y0, g.y1)
        sortie.append({
            "libelle": libelle or "(ligne d'en-tête)",
            "role": "entete",
            "y0": round(g.y0, 1), "y1": round(g.y1, 1),
            "marques": [{
                "type": mode,
                "colonnes": [i],
                "libelles": [colonnes[i].libelle],
                "texte": None,
                "couleur": couleur_hex(couleurs[i]),
                "fusion_verticale": False,
            } for i in sorted(marques)],
        })
    return sortie


def lire_ligne(geo: GeometriePage, colonnes: list[Colonne], a: float, b: float,
               gauche_x0: float, type_axe: str, mode_lignes: str) -> tuple[dict | None, str]:
    """Lit une ligne de la grille. Renvoie (ligne, statut) ; ligne None => fin de grille."""
    zx0, zx1 = colonnes[0].x0, colonnes[-1].x1
    bornes = Rect(zx0 - 1, geo.rect.y0, zx1 + 1, geo.rect.y1)
    cy = (a + b) / 2

    # Cellule englobante de chaque colonne au centre de la ligne
    cellules, bordees = [], 0
    for c in colonnes:
        cel = geo.cellule_englobante(c.cx, cy, bornes)
        if cel.bordee["g"] and cel.bordee["d"]:
            bordees += 1
        else:
            cel.x0, cel.x1 = c.x0, c.x1
        if not cel.bordee["h"]:
            cel.y0 = a
        if not cel.bordee["b"]:
            cel.y1 = b
        cellules.append(cel)
    if mode_lignes == "traits" and bordees == 0:
        return None, "hors_tableau"  # plus aucun trait vertical : légende, pied de page…

    # Segments = colonnes consécutives partageant la même cellule (cellule fusionnée)
    segments = []
    for i, cel in enumerate(cellules):
        if segments and segments[-1]["cle"] == cel.cle():
            segments[-1]["colonnes"].append(i)
        else:
            segments.append({"cle": cel.cle(), "cel": cel, "colonnes": [i]})

    for s in segments:
        cel = s["cel"]
        mots = [m for m in geo.mots if cel.x0 - 0.5 <= m.cx <= cel.x1 + 0.5
                and cel.y0 - 0.5 <= m.cy <= cel.y1 + 0.5 and zx0 - 0.5 <= m.cx <= zx1 + 0.5]
        s["texte"] = texte_mots(mots)
        teintes = [geo.couleur_au_point(colonnes[i].cx, cy) for i in s["colonnes"]]
        s["couleur"] = Counter(teintes).most_common(1)[0][0]
        s["fusion_verticale"] = cel.y0 < a - 2 or cel.y1 > b + 2

    libelle = libelle_gauche(geo, gauche_x0, zx0, a, b)
    croix = any(RE_CROIX.match(s["texte"]) for s in segments)
    coloree = any(s["couleur"] is not None for s in segments)
    if len(segments) == 1 and not coloree and not croix:
        if segments[0]["texte"]:
            # Texte pleine largeur sans couleur : pour un axe de mois c'est la consigne qui suit
            # l'en-tête (« en dehors des périodes sensibles ») ; pour un axe d'années, c'est une
            # bande légitime si la ligne a un libellé.
            if type_axe == "mois" or not libelle:
                return None, "texte_pleine_largeur"
        elif mode_lignes != "traits" or bordees == 0:
            return None, "ligne_vide_non_bordee"

    marques_remp, mode = regle_remplissage([s["couleur"] for s in segments],
                                            [len(s["colonnes"]) for s in segments])
    aucune_saisie = all(not s["texte"] for s in segments)
    marques = []
    for k, s in enumerate(segments):
        t = s["texte"]
        if RE_CROIX.match(t):
            type_ = "croix"
        elif t and len(s["colonnes"]) >= 2:
            type_ = "bande"
        elif t:
            type_ = "texte"
        elif k in marques_remp:
            type_ = mode
        elif mode == "uniforme" and aucune_saisie:
            type_ = "remplissage_uniforme"   # toute la ligne colorée, sans texte : ambigu
        else:
            type_ = None
        s["type"] = type_
        if type_:
            marques.append({
                "type": type_,
                "colonnes": s["colonnes"],
                "libelles": [colonnes[i].libelle for i in s["colonnes"]],
                "texte": t or None,
                "couleur": couleur_hex(s["couleur"]),
                "fusion_verticale": s["fusion_verticale"],
            })

    return {
        "libelle": libelle,
        "role": "corps",
        "y0": round(a, 1), "y1": round(b, 1),
        "segments": [{
            "colonnes": s["colonnes"], "texte": s["texte"] or None,
            "couleur": couleur_hex(s["couleur"]), "type": s["type"],
            "fusion_verticale": s["fusion_verticale"],
        } for s in segments],
        "marques": marques,
    }, "ok"


def _limites_lignes(geo: GeometriePage, colonnes: list[Colonne], y_debut: float, y_fin: float) -> tuple[list[float], str]:
    zx0, zx1 = colonnes[0].x0, colonnes[-1].x1
    larg_min = min(c.largeur for c in colonnes)
    ys = geo.traits_horizontaux_dans(zx0, zx1, y_debut - 1.5, y_fin, recouvrement_min=0.8 * larg_min)
    if not ys or ys[0] > y_debut + 2:
        ys = [y_debut] + ys
    if len(ys) >= 2:
        return ys, "traits"
    # Sans bordures : lignes de texte et aplats sous l'en-tête, dans la zone temporelle
    intervalles = []
    for m in geo.mots:
        if zx0 <= m.cx <= zx1 and y_debut < m.cy < y_fin:
            intervalles.append((m.y0 - 2, m.y1 + 2))
    for ap in geo.aplats:
        r = ap.rect
        if r.x0 >= zx0 - 2 and r.x1 <= zx1 + 2 and y_debut - 1 < r.y0 < y_fin and r.height < HAUTEUR_MAX_LIGNE:
            intervalles.append((r.y0, r.y1))
    intervalles.sort()
    fusion: list[list[float]] = []
    for a, b in intervalles:
        if fusion and a <= fusion[-1][1] + 1:
            fusion[-1][1] = max(fusion[-1][1], b)
        else:
            if (fusion and a - fusion[-1][1] > 30) or (not fusion and a - y_debut > 30):
                break
            fusion.append([a, b])
    bornes = [y_debut]
    for a, b in fusion:
        bornes += [max(a, bornes[-1]), b]
    return sorted(set(round(y, 2) for y in bornes)), "texte"


# ---------------------------------------------------------------------------- orchestration
def detecter_grilles(geo: GeometriePage, num_page: int) -> tuple[list[dict], list[Graine]]:
    graines = detecter_graines(geo)
    groupes = grouper_graines(graines)
    construits = []
    for groupe in groupes:
        colonnes, meta = construire_colonnes(geo, groupe)
        if len(colonnes) < (6 if groupe[0].type_axe == "mois" else 4):
            continue
        construits.append((groupe, colonnes, meta))

    construits.sort(key=lambda t: t[2]["bandeau"]["y0"])
    grilles: list[dict] = []
    for k, (groupe, colonnes, meta) in enumerate(construits):
        type_axe = groupe[0].type_axe
        zx0, zx1 = colonnes[0].x0, colonnes[-1].x1
        bandeau = meta["bandeau"]
        # Fin de recherche : prochain axe recouvrant la même zone, sinon bas de page
        y_fin = geo.rect.y1
        for _, cols2, meta2 in construits[k + 1:]:
            if min(cols2[-1].x1, zx1) - max(cols2[0].x0, zx0) > 0:
                y_fin = meta2["bandeau"]["y0"]
                break
        # Bord gauche du tableau : début des traits horizontaux qui passent sous l'en-tête
        sous = [t for t in geo.horizontaux
                if bandeau["y0"] - 2.5 <= t.pos <= bandeau["y1"] + 2.5 and t.a < zx0 - 5 and t.b > zx0 - 5]
        gauche_x0 = min((t.a for t in sous), default=max(geo.rect.x0, zx0 - 250))

        entete = lire_entete(geo, groupe, colonnes, gauche_x0)
        ys, mode_lignes = _limites_lignes(geo, colonnes, bandeau["y1"], y_fin)
        lignes, fin = [], "fin_des_traits"
        for a, b in zip(ys, ys[1:]):
            if b - a < 4:
                continue
            if b - a > HAUTEUR_MAX_LIGNE:
                fin = "ligne_trop_haute"
                break
            if a >= y_fin - 1:
                fin = "axe_suivant"
                break
            ligne, statut = lire_ligne(geo, colonnes, a, b, gauche_x0, type_axe, mode_lignes)
            if ligne is None:
                fin = statut
                break
            lignes.append(ligne)

        y_bas = lignes[-1]["y1"] if lignes else bandeau["y1"]
        n_bordees = sum(c.bordee for c in colonnes)
        confiance = "haute" if meta["mode_colonnes"] == "bordures" and mode_lignes == "traits" else "moyenne"
        grilles.append({
            "id": f"p{num_page:03d}_g{len(grilles) + 1}",
            "page": num_page,
            "type_axe": type_axe,
            "bbox": [round(gauche_x0, 1), round(bandeau["y0"], 1), round(zx1, 1), round(y_bas, 1)],
            "zone_temporelle": [round(zx0, 1), round(zx1, 1)],
            "confiance": confiance,
            "mode_colonnes": meta["mode_colonnes"],
            "mode_lignes": mode_lignes,
            "fin_de_lecture": fin,
            "colonnes_bordees": f"{n_bordees}/{len(colonnes)}",
            "colonnes": [{
                "i": c.i, "x0": round(c.x0, 1), "x1": round(c.x1, 1),
                "libelle": c.libelle, **{k: v for k, v in c.info.items() if k != "libelle"},
            } for c in colonnes],
            "lignes": entete + lignes,
        })
    return grilles, graines
