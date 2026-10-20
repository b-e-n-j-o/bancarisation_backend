"""Génère un corpus de PDF synthétiques couvrant les variantes de mise en forme rencontrées dans
les plans de gestion, avec leur vérité terrain (attendus/*.json).

Chaque cas isole une difficulté : bordures en rectangles fins (Word) ou en traits tracés,
absence de bordures, mois abrégés ou en initiales, couleur minoritaire, cellules fusionnées,
en-tête stylé uniformément, grille en image, page scannée, distracteurs (prose, tableau budgétaire).

    python -m pdfvisuel.evaluation.generer_corpus corpus/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .mini_pdf import Document, Rect

NOIR = (0, 0, 0)
BLEU_CLAIR = (0.86, 0.90, 0.95)
VERT = (0.84, 0.89, 0.74)
VIOLET = (0.88, 0.85, 0.93)
ORANGE = (0.98, 0.80, 0.55)
GRIS = (0.90, 0.90, 0.90)
VERT_FONCE = (0.55, 0.75, 0.45)
MOIS = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août",
        "Septembre", "Octobre", "Novembre", "Décembre"]
MOIS_ABR = ["Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin", "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc."]


def tableau(page, x0, y0, largeurs, hauteurs, bordures="rect", aplats=None, textes=None, fusions=None, taille=7):
    """Dessine un tableau. aplats/textes : {(ligne, col): valeur} ; fusions : [(ligne, c0, c1)]."""
    aplats, textes, fusions = aplats or {}, textes or {}, fusions or []
    xs = [x0]
    for w in largeurs:
        xs.append(xs[-1] + w)
    ys = [y0]
    for h in hauteurs:
        ys.append(ys[-1] + h)
    cellules = []
    for r in range(len(hauteurs)):
        c = 0
        while c < len(largeurs):
            fin = next((f[2] for f in fusions if f[0] == r and f[1] == c), c)
            cellules.append((r, c, Rect(xs[c], ys[r], xs[fin + 1], ys[r + 1])))
            c = fin + 1
    for r, c, rect in cellules:
        if (r, c) in aplats:
            page.draw_rect(rect, color=None, fill=aplats[(r, c)])
    for r, c, rect in cellules:
        if bordures == "rect":
            e = 0.5
            for b in (Rect(rect.x0, rect.y0, rect.x1, rect.y0 + e), Rect(rect.x0, rect.y1 - e, rect.x1, rect.y1),
                      Rect(rect.x0, rect.y0, rect.x0 + e, rect.y1), Rect(rect.x1 - e, rect.y0, rect.x1, rect.y1)):
                page.draw_rect(b, color=None, fill=NOIR)
        elif bordures == "traits":
            for p1, p2 in ((rect.tl, rect.tr), (rect.bl, rect.br), (rect.tl, rect.bl), (rect.tr, rect.br)):
                page.draw_line(p1, p2, color=NOIR, width=0.6)
        if (r, c) in textes:
            t = textes[(r, c)]
            zone = rect + (1.5, 1, -1.5, -1)
            if page.insert_textbox(zone, t, fontsize=taille, fontname="helv", align=1) < 0:
                page.insert_textbox(zone, t, fontsize=taille - 2, fontname="helv", align=1)
    return xs, ys


def titre(page, y, texte, taille=11):
    page.insert_text((50, y), texte, fontsize=taille, fontname="helv")


def paragraphe(page, y, texte, largeur=480):
    page.insert_textbox(Rect(50, y, 50 + largeur, y + 120), texte, fontsize=9, fontname="helv")


# ---------------------------------------------------------------------------- cas
def c01(doc):
    """Fiche type Word : mois surlignés dans l'en-tête (bordures en rectangles fins)."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "RG1 : Réouverture du milieu")
    larg = [120] + [50] * 12
    textes = {(0, 0): "Période d'intervention", **{(0, i + 1): m for i, m in enumerate(MOIS)},
              (1, 0): "", (1, 1): "Réouverture : en dehors des périodes sensibles pour la faune"}
    aplats = {(0, i): BLEU_CLAIR for i in (3, 4, 7, 8)}
    tableau(p, 50, 80, larg, [18, 30], "rect", aplats, textes, fusions=[(1, 1, 12)])
    return {"grilles": [{"page": 1, "type_axe": "mois", "lignes": [
        {"role": "entete", "libelle_contient": "intervention", "couleur": [3, 4, 7, 8]}]}],
        "exhaustif": True}


def c02(doc):
    """Gantt mensuel : en-tête blanc, cellules colorées dans le corps, bordures en traits tracés."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "Calendrier des interventions")
    larg = [130] + [48] * 12
    textes = {(0, 0): "Action", **{(0, i + 1): m for i, m in enumerate(MOIS)},
              (1, 0): "Fauche tardive", (2, 0): "Suivi avifaune", (3, 0): "Arrachage invasives"}
    aplats = {(1, 9): VERT, (1, 10): VERT, (2, 4): ORANGE, (2, 5): ORANGE, (2, 6): ORANGE, (3, 10): VERT}
    tableau(p, 50, 80, larg, [18, 18, 18, 18], "traits", aplats, textes)
    return {"grilles": [{"page": 1, "type_axe": "mois", "lignes": [
        {"libelle_contient": "Fauche", "couleur": [9, 10]},
        {"libelle_contient": "avifaune", "couleur": [4, 5, 6]},
        {"libelle_contient": "Arrachage", "couleur": [10]}]}], "exhaustif": True}


def c03(doc):
    """Programmation pluriannuelle : croix, bandes fusionnées colorées, ligne vide, bande non colorée."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "Tableau 12 : Programmation des actions")
    n = 11
    larg = [60, 140] + [42] * n
    textes = {(0, 0): "Code", (0, 1): "Action", **{(0, i + 2): ("N" if i == 0 else f"N+{i}") for i in range(n)},
              **{(1, i + 2): str(2026 + i) for i in range(n)},
              (2, 0): "TU1", (2, 1): "Travaux de restauration", (2, 2): "X",
              (3, 0): "MG1", (3, 1): "Entretien par fauche", (3, 3): "1 fauche annuelle en septembre",
              (4, 0): "SE1", (4, 1): "Suivi botanique", (4, 2): "X", (4, 3): "X", (4, 4): "X", (4, 7): "X", (4, 12): "X",
              (5, 0): "SE2", (5, 1): "Bilans", (5, 7): "X", (5, 12): "X",
              (6, 0): "TE1", (6, 1): "Réserve (non programmé)",
              (7, 0): "AD1", (7, 1): "Animation", (7, 2): "Veille annuelle du site"}
    aplats = {(3, 3): VERT, (0, 0): BLEU_CLAIR, (0, 1): BLEU_CLAIR, **{(0, i + 2): BLEU_CLAIR for i in range(n)},
              **{(1, i + 2): BLEU_CLAIR for i in range(n)}}
    fus = [(3, 3, 12), (7, 2, 12)]
    tableau(p, 40, 80, larg, [16, 16, 22, 22, 22, 22, 22, 22], "rect", aplats, textes, fus)
    N = lambda *k: [f"N+{x}" if x else "N" for x in k]
    return {"grilles": [{"page": 1, "type_axe": "annees", "lignes": [
        {"libelle_contient": "TU1", "croix": N(0)},
        {"libelle_contient": "MG1", "bande": [N(*range(1, 11))]},
        {"libelle_contient": "SE1", "croix": N(0, 1, 2, 5, 10)},
        {"libelle_contient": "SE2", "croix": N(5, 10)},
        {"libelle_contient": "TE1"},
        {"libelle_contient": "AD1", "bande": [N(*range(0, 11))]}]}], "exhaustif": True}


def c04(doc):
    """Mois abrégés ; toute la ligne est grisée et les mois marqués sont d'une autre couleur."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "SE1 : Suivi des chiroptères — période d'intervention")
    larg = [130] + [45] * 12
    textes = {(0, 0): "", **{(0, i + 1): m for i, m in enumerate(MOIS_ABR)}, (1, 0): "Écoutes nocturnes"}
    aplats = {**{(1, i): GRIS for i in range(1, 13)}, (1, 6): VERT_FONCE, (1, 7): VERT_FONCE, (1, 8): VERT_FONCE}
    tableau(p, 50, 80, larg, [18, 18], "rect", aplats, textes)
    return {"grilles": [{"page": 1, "type_axe": "mois", "lignes": [
        {"libelle_contient": "Écoutes", "couleur": [6, 7, 8]}]}], "exhaustif": True}


def c05(doc):
    """Mois en initiales J F M A M J J A S O N D."""
    p = doc.new_page(width=595, height=842)
    titre(p, 50, "Calendrier écologique")
    larg = [150] + [24] * 12
    textes = {(0, 0): "Opération", **{(0, i + 1): l for i, l in enumerate("JFMAMJJASOND")},
              (1, 0): "Abattage des arbres", (2, 0): "Curage des mares"}
    aplats = {(1, 1): VERT, (1, 2): VERT, (1, 12): VERT, (2, 10): ORANGE, (2, 11): ORANGE}
    tableau(p, 50, 80, larg, [16, 16, 16], "rect", aplats, textes)
    return {"grilles": [{"page": 1, "type_axe": "mois", "lignes": [
        {"libelle_contient": "Abattage", "couleur": [1, 2, 12]},
        {"libelle_contient": "Curage", "couleur": [10, 11]}]}], "exhaustif": True}


def c06(doc):
    """Aucune bordure : libellés de mois et barres colorées seules."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "Fenêtres d'intervention")
    larg = [140] + [50] * 12
    textes = {**{(0, i + 1): m for i, m in enumerate(MOIS)}, (1, 0): "Fauche tardive"}
    aplats = {(1, 7): VERT, (1, 8): VERT}
    tableau(p, 50, 80, larg, [18, 18], "aucune", aplats, textes)
    return {"grilles": [{"page": 1, "type_axe": "mois", "lignes": [
        {"libelle_contient": "Fauche", "couleur": [7, 8]}]}], "exhaustif": True}


def _rendu_grille():
    tmp = Document()
    p = tmp.new_page(width=700, height=120)
    larg = [120] + [46] * 12
    textes = {(0, 0): "Action", **{(0, i + 1): m for i, m in enumerate(MOIS)}, (1, 0): "Fauche", (2, 0): "Suivi"}
    tableau(p, 10, 10, larg, [18, 18, 18], "traits", {(1, 9): VERT, (2, 5): ORANGE, (2, 6): ORANGE}, textes)
    return tmp.rendre(0, dpi=130, zone=Rect(5, 5, 690, 70))


def c07(doc):
    """Grille collée en image (capture) : rien n'est lisible dans la couche vectorielle."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "RG3 : Calendrier des interventions :")
    p.insert_image(Rect(50, 70, 750, 140), _rendu_grille())
    paragraphe(p, 170, "Les interventions sont réalisées selon le calendrier ci-dessus.")
    return {"grilles": [], "exhaustif": True, "images": [{"page": 1, "classe": "document_raster"}],
            "routage_vision": [1]}


def c08(doc):
    """Page entièrement numérisée (aucune couche texte)."""
    tmp = Document()
    q = tmp.new_page(width=595, height=842)
    titre(q, 60, "Plan de gestion — fiche MG2")
    paragraphe(q, 90, "Fauche annuelle en septembre ou octobre, exportation des produits de fauche. " * 4)
    tableau(q, 50, 220, [110] + [36] * 12, [16, 16], "traits", {(1, 9): VERT, (1, 10): VERT},
            {(0, 0): "", **{(0, i + 1): m[:4] for i, m in enumerate(MOIS)}, (1, 0): "Fauche"})
    pix = tmp.rendre(0, dpi=110)
    p = doc.new_page(width=595, height=842)
    p.insert_image(p.rect, pix)
    return {"grilles": [], "exhaustif": True, "pages_scannees": [1], "routage_vision": [1]}


def c09(doc):
    """Distracteurs : prose riche en mois et en N+k, tableau d'années en colonne."""
    p = doc.new_page(width=595, height=842)
    titre(p, 50, "Modalités de suivi")
    paragraphe(p, 70, "Les suivis auront lieu en janvier, février, mars, avril, mai et juin de l'année N, puis "
                      "à N+1, N+2, N+3, N+5, N+10, N+15 et N+20. Les comités se réuniront de N à N+5 puis à N+10, "
                      "N+20 et N+30. Les rapports sont transmis en 2026, 2027, 2028, 2030 et 2035.")
    tableau(p, 50, 220, [100, 150], [16] * 6, "rect", None,
            {(0, 0): "Année", (0, 1): "Opération", (1, 0): "2026", (1, 1): "Travaux", (2, 0): "2027",
             (2, 1): "Suivi", (3, 0): "2028", (3, 1): "Suivi", (4, 0): "2030", (4, 1): "Bilan",
             (5, 0): "2035", (5, 1): "Bilan"})
    return {"grilles": [], "exhaustif": True}


def c10(doc):
    """Tableau budgétaire avec années en colonnes : grille reconnue, aucune croix ni couleur."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "Tableau 43 : Coûts prévisionnels (€ HT)")
    larg = [160] + [70] * 6
    textes = {(0, 0): "Poste", **{(0, i + 1): str(2025 + i) for i in range(6)},
              (1, 0): "Fauche", **{(1, i + 1): "1 200" for i in range(6)},
              (2, 0): "Suivis", (2, 1): "2 400", (2, 3): "2 400", (2, 6): "2 400"}
    aplats = {(0, i): BLEU_CLAIR for i in range(7)}
    tableau(p, 50, 80, larg, [18, 18, 18], "rect", aplats, textes)
    return {"grilles": [{"page": 1, "type_axe": "annees", "lignes": [
        {"libelle_contient": "Fauche"}, {"libelle_contient": "Suivis"}]}], "exhaustif": True}


def c11(doc):
    """En-tête de mois entièrement coloré (style) et croix dans le corps : la couleur d'en-tête
    ne doit PAS être lue comme une marque."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "ES2 : Suivi de l'avifaune")
    larg = [130] + [48] * 12
    textes = {(0, 0): "Mois", **{(0, i + 1): m for i, m in enumerate(MOIS)},
              (1, 0): "Points d'écoute", (1, 4): "X", (1, 5): "X", (1, 6): "X"}
    aplats = {(0, i): VIOLET for i in range(13)}
    tableau(p, 50, 80, larg, [18, 18], "rect", aplats, textes)
    return {"grilles": [{"page": 1, "type_axe": "mois", "lignes": [
        {"libelle_contient": "écoute", "croix": [4, 5, 6]}]}], "exhaustif": True}


def c12(doc):
    """Deux couleurs dans la même ligne (deux opérations) et une cellule fusionnée colorée sans texte."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "MG1 : Gestion de la prairie")
    larg = [130] + [48] * 12
    textes = {(0, 0): "", **{(0, i + 1): m for i, m in enumerate(MOIS)}, (1, 0): "Fauche / griffage"}
    aplats = {(1, 3): VERT, (1, 4): VERT, (1, 8): ORANGE}
    tableau(p, 50, 80, larg, [18, 18], "rect", aplats, textes, fusions=[(1, 3, 4)])
    return {"grilles": [{"page": 1, "type_axe": "mois", "lignes": [
        {"libelle_contient": "Fauche", "couleur": [3, 4, 8]}]}], "exhaustif": True}


def c13(doc):
    """Frise avec colonne « État zéro » avant N (format courant chez certains BE)."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "SE1 : Suivi de la végétation — frise de périodicité")
    larg = [140, 60] + [44] * 8
    textes = {(0, 0): "Suivi", (0, 1): "État zéro", **{(0, i + 2): ("N" if i == 0 else f"N+{i}") for i in range(8)},
              (1, 0): "Relevés floristiques", (1, 1): "X", (1, 3): "X", (1, 5): "X", (1, 9): "X"}
    aplats = {(0, i): BLEU_CLAIR for i in range(10)}
    tableau(p, 50, 80, larg, [18, 18], "rect", aplats, textes)
    return {"grilles": [{"page": 1, "type_axe": "annees", "lignes": [
        {"libelle_contient": "Relevés", "croix": ["E0", "N+1", "N+3", "N+7"]}]}], "exhaustif": True}


def c14(doc):
    """Frise de 30 ans dessinée sur deux rangées (2023-2037 puis 2038-2052), sans libellé de
    ligne : le titre d'action est au-dessus du tableau (format des fiches du Porge)."""
    p = doc.new_page(width=842, height=595)
    titre(p, 50, "SE1 : Suivi piézométrique des sondes")
    paragraphe(p, 62, "Les relevés des sondes sont réalisés deux fois par an sur les cinq premières années, puis tous les cinq ans.")
    larg = [46] * 15
    vert = (0.0, 0.69, 0.31)
    r1 = {(0, i): str(2023 + i) for i in range(15)}
    a1 = {(1, i): vert for i in (0, 1, 2, 3, 4, 9, 14)}
    tableau(p, 60, 100, larg, [16, 16], "rect", a1, r1)
    r2 = {(0, i): str(2038 + i) for i in range(15)}
    a2 = {(1, i): vert for i in (4, 9, 14)}
    tableau(p, 60, 140, larg, [16, 16], "rect", a2, r2)
    paragraphe(p, 185, "Le rapport de suivi est transmis chaque année de relevé à la DDTM.")
    annees = [str(a) for a in (2023, 2024, 2025, 2026, 2027, 2032, 2037, 2042, 2047, 2052)]
    return {"grilles": [{"page": 1, "type_axe": "annees", "lignes": [
        {"libelle_contient": "", "couleur": annees}]}], "exhaustif": True}


CAS = [c01, c02, c03, c04, c05, c06, c07, c08, c09, c10, c11, c12, c13, c14]


def generer(dossier: Path) -> None:
    (dossier / "attendus").mkdir(parents=True, exist_ok=True)
    for f in CAS:
        doc = Document()
        att = f(doc)
        nom = f"{f.__name__}.pdf"
        doc.save(dossier / nom)
        att = {"pdf": nom, "description": (f.__doc__ or "").strip(), **att}
        (dossier / "attendus" / f"{f.__name__}.json").write_text(json.dumps(att, ensure_ascii=False, indent=1))
    print(f"{len(CAS)} cas générés dans {dossier}")


if __name__ == "__main__":
    generer(Path(sys.argv[1] if len(sys.argv) > 1 else "corpus"))
