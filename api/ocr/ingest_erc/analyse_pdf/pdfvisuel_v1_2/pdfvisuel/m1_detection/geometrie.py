"""Géométrie vectorielle d'une page PDF : mots, traits (bordures), aplats colorés.

Tout est lu dans la couche vectorielle du PDF via pdfplumber (MIT, sur pdfminer.six) ; les rendus
d'image passent par pypdfium2 (Apache-2.0 / BSD-3). Aucune dépendance AGPL, aucun appel API.

Notions :
- un *trait* est un segment horizontal ou vertical (bordure de tableau). Word/LibreOffice
  dessinent les bordures soit comme des rectangles remplis très fins, soit comme des
  segments tracés (stroke). Les deux sont normalisés ici en `Trait`.
- un *aplat* est un rectangle rempli d'au moins 3 pt de côté (trame de cellule, bandeau).
- la *cellule englobante* d'un point est délimitée par le trait le plus proche de chaque
  côté. C'est ce qui permet de reconnaître une cellule fusionnée (même cellule pour
  plusieurs colonnes) sans dépendre d'un détecteur de tableaux.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

import pdfplumber
import pypdfium2 as pdfium

EPAISSEUR_MAX_TRAIT = 1.6   # pt : en dessous, un rectangle rempli est un trait
COTE_MIN_APLAT = 3.0        # pt : au dessus, un rectangle rempli est un aplat
SEUIL_BLANC = 0.97          # composantes RGB >= seuil => blanc (pas une marque)


@dataclass
class Mot:
    x0: float
    y0: float
    x1: float
    y1: float
    texte: str
    bloc: int = 0
    ligne: int = 0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class Trait:
    """Segment axial. Vertical : x fixe, [a, b] en y. Horizontal : y fixe, [a, b] en x."""
    pos: float
    a: float
    b: float


@dataclass
class Aplat:
    rect: "Rect"
    couleur: tuple  # RGB 0..1

    @property
    def aire(self) -> float:
        return self.rect.width * self.rect.height


@dataclass
class Cellule:
    x0: float
    y0: float
    x1: float
    y1: float
    bordee: dict = field(default_factory=dict)  # côté -> True si un vrai trait le borde

    def cle(self) -> tuple:
        return (round(self.x0, 1), round(self.y0, 1), round(self.x1, 1), round(self.y1, 1))

    def contient(self, x: float, y: float, marge: float = 0.5) -> bool:
        return self.x0 - marge <= x <= self.x1 + marge and self.y0 - marge <= y <= self.y1 + marge


def normaliser(t: str) -> str:
    """Minuscules, sans accents, sans ponctuation de bord."""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower().strip(" .,;:()[]’'\"«»")


def est_blanc(couleur) -> bool:
    return couleur is None or all(c >= SEUIL_BLANC for c in couleur)


def couleur_hex(couleur) -> str | None:
    if couleur is None:
        return None
    return "#" + "".join(f"{int(round(c * 255)):02X}" for c in couleur[:3])


def _fusionner_traits(traits: list[Trait], tol_pos: float = 0.8, tol_gap: float = 1.5) -> list[Trait]:
    """Fusionne les segments colinéaires qui se touchent (bordures dessinées cellule par cellule)."""
    # 1) regrouper les traits par position (à tol_pos près), 2) fusionner les intervalles
    groupes: list[list[Trait]] = []
    for t in sorted(traits, key=lambda t: t.pos):
        if groupes and t.pos - groupes[-1][-1].pos <= tol_pos:
            groupes[-1].append(t)
        else:
            groupes.append([t])
    sortie: list[Trait] = []
    for g in groupes:
        pos = sum(t.pos for t in g) / len(g)
        courant: Trait | None = None
        for t in sorted(g, key=lambda t: t.a):
            if courant and t.a <= courant.b + tol_gap:
                courant.b = max(courant.b, t.b)
            else:
                courant = Trait(pos, t.a, t.b)
                sortie.append(courant)
    return sortie


class Rect:
    """Rectangle en coordonnées « haut-gauche » (y croît vers le bas), comme pdfplumber."""
    __slots__ = ("x0", "y0", "x1", "y1")

    def __init__(self, x0: float, y0: float, x1: float, y1: float):
        self.x0, self.y0, self.x1, self.y1 = float(x0), float(y0), float(x1), float(y1)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def is_empty(self) -> bool:
        return self.x1 <= self.x0 or self.y1 <= self.y0

    def __and__(self, o: "Rect") -> "Rect":
        return Rect(max(self.x0, o.x0), max(self.y0, o.y0), min(self.x1, o.x1), min(self.y1, o.y1))

    def __iter__(self):
        return iter((self.x0, self.y0, self.x1, self.y1))


def couleur_rgb(c) -> tuple | None:
    """Couleur pdfminer -> RGB 0..1. pdfminer renvoie la couleur dans l'espace d'origine :
    gris (1 valeur), RGB (3), CMJN (4), ou un nom de motif (non interprétable -> None)."""
    if c is None or isinstance(c, str):
        return None
    if isinstance(c, (int, float)):
        c = (c,)
    try:
        c = tuple(float(x) for x in c)
    except (TypeError, ValueError):
        return None
    if len(c) == 1:
        return (c[0], c[0], c[0])
    if len(c) == 3:
        return c
    if len(c) == 4:
        cy, m, j, k = c
        return ((1 - cy) * (1 - k), (1 - m) * (1 - k), (1 - j) * (1 - k))
    return None


class DocumentPDF:
    """Ouvre le PDF une fois avec pdfplumber (géométrie) et pypdfium2 (rendus)."""

    def __init__(self, chemin: str):
        self.chemin = str(chemin)
        self.plumber = pdfplumber.open(self.chemin)
        self.pdfium = pdfium.PdfDocument(self.chemin)

    def __len__(self) -> int:
        return len(self.plumber.pages)

    def page(self, num: int) -> "GeometriePage":
        """num : numéro de page en base 1."""
        return GeometriePage(self.plumber.pages[num - 1], self, num)

    def rendre(self, num: int, zone: "Rect | None" = None, dpi: int = 72):
        """Image PIL RGB de la page (ou d'une zone en coordonnées haut-gauche)."""
        page = self.pdfium[num - 1]
        w, h = page.get_size()
        crop = (0, 0, 0, 0)
        if zone is not None:
            z = zone & Rect(0, 0, w, h)
            crop = (z.x0, h - z.y1, w - z.x1, z.y0)   # gauche, bas, droite, haut à retirer
        img = page.render(scale=dpi / 72, crop=crop).to_pil().convert("RGB")
        return img

    def fermer(self) -> None:
        self.plumber.close()
        self.pdfium.close()


def est_rectangle(o: dict, tol: float = 0.6) -> bool:
    """Chemin dont tous les points sont des coins de sa boîte englobante : c'est un rectangle,
    quel que soit le nombre de points (« re h f » donne une courbe de 6 points chez pdfminer)."""
    pts = o.get("pts") or []
    if len(pts) < 4:
        return False
    xs, ys = (o["x0"], o["x1"]), (o["top"], o["bottom"])
    return all(min(abs(x - v) for v in xs) <= tol and min(abs(y - v) for v in ys) <= tol for x, y in pts)


def extraire_mots(page) -> list[Mot]:
    """Mots de la page. Les caractères droits passent par l'extracteur standard ; les caractères
    pivotés (libellés verticaux « Phasage prévisionnel ») sont regroupés à part, dans le sens de
    lecture donné par leur matrice : lu de bas en haut (b > 0) ou de haut en bas (b < 0)."""
    reglages = dict(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
    def pivote(o) -> bool:
        # le drapeau `upright` de pdfminer est parfois faux : on lit la matrice du caractère
        a, b = (o.get("matrix") or (1, 0))[:2]
        return abs(b) > abs(a)

    droits = page.filter(lambda o: o.get("object_type") != "char" or not pivote(o))
    mots = [Mot(w["x0"], w["top"], w["x1"], w["bottom"], w["text"])
            for w in droits.extract_words(**reglages) if w["text"].strip()]
    # Caractères pivotés : même colonne (x0 proche), ordonnés dans le sens de lecture,
    # coupés aux espaces et aux trous de plus de 3 pt.
    pivotes = sorted((c for c in page.chars if pivote(c)), key=lambda c: (round(c["x0"]), c["top"]))
    colonnes: list[list[dict]] = []
    for c in pivotes:
        if colonnes and abs(colonnes[-1][0]["x0"] - c["x0"]) < 1.5:
            colonnes[-1].append(c)
        else:
            colonnes.append([c])
    for col in colonnes:
        montant = (col[0].get("matrix") or (0, 1))[1] > 0     # lu de bas en haut
        col.sort(key=lambda c: -c["top"] if montant else c["top"])
        mot: list[dict] = []

        def vider():
            if mot:
                mots.append(Mot(min(c["x0"] for c in mot), min(c["top"] for c in mot),
                                max(c["x1"] for c in mot), max(c["bottom"] for c in mot),
                                "".join(c["text"] for c in mot)))
                mot.clear()
        for c in col:
            if not c["text"].strip():
                vider()
                continue
            if mot:
                prec = mot[-1]
                trou = (prec["top"] - c["bottom"]) if montant else (c["top"] - prec["bottom"])
                if trou > 3:
                    vider()
            mot.append(c)
        vider()
    return mots


class GeometriePage:
    def __init__(self, page, document: DocumentPDF | None = None, num: int = 1):
        self.page = page
        self.document = document
        self.num = num
        self.rect = Rect(0, 0, float(page.width), float(page.height))
        self.mots: list[Mot] = extraire_mots(page)
        self.mots.sort(key=lambda m: (round(m.y0), m.x0))
        self.aplats: list[Aplat] = []
        verticaux: list[Trait] = []
        horizontaux: list[Trait] = []

        # Rectangles (et chemins fermés assimilables à des rectangles)
        objets = list(page.rects) + [c for c in page.curves if est_rectangle(c)]
        for o in objets:
            r = Rect(o["x0"], o["top"], o["x1"], o["bottom"])
            fill = couleur_rgb(o.get("non_stroking_color")) if o.get("fill") else None
            if fill is not None:
                if r.width <= EPAISSEUR_MAX_TRAIT and r.height > 2:
                    verticaux.append(Trait((r.x0 + r.x1) / 2, r.y0, r.y1))
                elif r.height <= EPAISSEUR_MAX_TRAIT and r.width > 2:
                    horizontaux.append(Trait((r.y0 + r.y1) / 2, r.x0, r.x1))
                elif r.width >= COTE_MIN_APLAT and r.height >= COTE_MIN_APLAT:
                    self.aplats.append(Aplat(r, fill))
            if o.get("stroke") and r.width > 2 and r.height > 2:
                horizontaux += [Trait(r.y0, r.x0, r.x1), Trait(r.y1, r.x0, r.x1)]
                verticaux += [Trait(r.x0, r.y0, r.y1), Trait(r.x1, r.y0, r.y1)]
        # Chemins tracés à plusieurs segments (bordures en ligne brisée) : segment par segment
        for c in page.curves:
            if c.get("stroke") and not est_rectangle(c):
                pts = c.get("pts") or []
                for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                    if abs(xa - xb) < 1 and abs(ya - yb) > 2:
                        verticaux.append(Trait((xa + xb) / 2, min(ya, yb), max(ya, yb)))
                    elif abs(ya - yb) < 1 and abs(xa - xb) > 2:
                        horizontaux.append(Trait((ya + yb) / 2, min(xa, xb), max(xa, xb)))
        # Segments tracés
        for l in page.lines:
            x0, x1, y0, y1 = l["x0"], l["x1"], l["top"], l["bottom"]
            if x1 - x0 < 1 and y1 - y0 > 2:
                verticaux.append(Trait((x0 + x1) / 2, y0, y1))
            elif y1 - y0 < 1 and x1 - x0 > 2:
                horizontaux.append(Trait((y0 + y1) / 2, x0, x1))

        self.verticaux = _fusionner_traits(verticaux)
        self.horizontaux = _fusionner_traits(horizontaux)

    def images(self) -> list[Rect]:
        return [Rect(i["x0"], i["top"], i["x1"], i["bottom"]) for i in self.page.images]

    def rendre(self, zone: Rect | None = None, dpi: int = 72):
        return self.document.rendre(self.num, zone, dpi)

    # ------------------------------------------------------------------ lignes de texte
    def lignes_de_mots(self, mots: list[Mot] | None = None) -> list[list[Mot]]:
        """Regroupe les mots par ligne visuelle (centre vertical proche)."""
        mots = sorted(mots if mots is not None else self.mots, key=lambda m: (m.cy, m.x0))
        lignes: list[list[Mot]] = []
        for m in mots:
            if lignes:
                ref = lignes[-1]
                cy = sum(x.cy for x in ref) / len(ref)
                h = max(2.0, sum(x.h for x in ref) / len(ref))
                if abs(m.cy - cy) <= 0.45 * h:
                    ref.append(m)
                    continue
            lignes.append([m])
        return [sorted(l, key=lambda m: m.x0) for l in lignes]

    # ------------------------------------------------------------------ cellules
    def cellule_englobante(self, x: float, y: float, bornes: Rect | None = None) -> Cellule:
        """Cellule délimitée par le trait le plus proche de chaque côté du point (x, y).

        Un côté sans trait est borné par `bornes` (ou la page) et noté non bordé.
        """
        b = bornes or self.rect
        g = max((t.pos for t in self.verticaux
                 if b.x0 - 1 <= t.pos <= x - 0.5 and t.a - 1 <= y <= t.b + 1), default=None)
        d = min((t.pos for t in self.verticaux
                 if x + 0.5 <= t.pos <= b.x1 + 1 and t.a - 1 <= y <= t.b + 1), default=None)
        h = max((t.pos for t in self.horizontaux
                 if b.y0 - 1 <= t.pos <= y - 0.5 and t.a - 1 <= x <= t.b + 1), default=None)
        bas = min((t.pos for t in self.horizontaux
                   if y + 0.5 <= t.pos <= b.y1 + 1 and t.a - 1 <= x <= t.b + 1), default=None)
        return Cellule(
            g if g is not None else b.x0,
            h if h is not None else b.y0,
            d if d is not None else b.x1,
            bas if bas is not None else b.y1,
            {"g": g is not None, "d": d is not None, "h": h is not None, "b": bas is not None},
        )

    def couleur_au_point(self, x: float, y: float) -> tuple | None:
        """Couleur de l'aplat le plus petit contenant le point ; None si blanc ou rien."""
        candidats = [a for a in self.aplats
                     if a.rect.x0 - 0.3 <= x <= a.rect.x1 + 0.3 and a.rect.y0 - 0.3 <= y <= a.rect.y1 + 0.3]
        if not candidats:
            return None
        c = min(candidats, key=lambda a: a.aire).couleur
        return None if est_blanc(c) else c

    def mots_dans(self, x0: float, y0: float, x1: float, y1: float) -> list[Mot]:
        return [m for m in self.mots if x0 - 0.5 <= m.cx <= x1 + 0.5 and y0 - 0.5 <= m.cy <= y1 + 0.5]

    def traits_horizontaux_dans(self, x0: float, x1: float, y_min: float, y_max: float,
                                recouvrement_min: float) -> list[float]:
        """Ordonnées des traits horizontaux qui couvrent au moins `recouvrement_min` pt de [x0, x1]."""
        ys = []
        for t in self.horizontaux:
            if y_min <= t.pos <= y_max:
                rec = min(t.b, x1) - max(t.a, x0)
                if rec >= recouvrement_min:
                    ys.append(t.pos)
        ys.sort()
        fusion: list[float] = []
        for y in ys:
            if fusion and y - fusion[-1] < 1.5:
                continue
            fusion.append(y)
        return fusion


def texte_mots(mots: list[Mot]) -> str:
    """Texte en ordre de lecture (lignes puis x)."""
    if not mots:
        return ""
    lignes: list[list[Mot]] = []
    for m in sorted(mots, key=lambda m: (m.cy, m.x0)):
        if lignes and abs(m.cy - lignes[-1][0].cy) <= 0.45 * max(2.0, m.h):
            lignes[-1].append(m)
        else:
            lignes.append([m])
    return " ".join(" ".join(x.texte for x in sorted(l, key=lambda m: m.x0)) for l in lignes)
