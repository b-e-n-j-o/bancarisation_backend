"""Petit adaptateur de dessin PDF sur reportlab (BSD), utilisé par le générateur de corpus.

Expose les quelques opérations dont les cas de test ont besoin, en coordonnées « haut-gauche »
(comme pdfplumber) : rectangles remplis, traits, texte, zone de texte centrée, image ; plus le
rendu d'une page en image (via pypdfium2) pour fabriquer les cas « grille en image » et « scan ».
"""
from __future__ import annotations

import io

import pypdfium2 as pdfium
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

POLICE = "Helvetica"


class Rect:
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = float(x0), float(y0), float(x1), float(y1)

    tl = property(lambda s: (s.x0, s.y0))
    tr = property(lambda s: (s.x1, s.y0))
    bl = property(lambda s: (s.x0, s.y1))
    br = property(lambda s: (s.x1, s.y1))
    width = property(lambda s: s.x1 - s.x0)
    height = property(lambda s: s.y1 - s.y0)

    def __add__(self, d):
        return Rect(self.x0 + d[0], self.y0 + d[1], self.x1 + d[2], self.y1 + d[3])


class Page:
    def __init__(self, largeur: float, hauteur: float):
        self.largeur, self.hauteur = largeur, hauteur
        self.rect = Rect(0, 0, largeur, hauteur)
        self.ops: list[tuple] = []

    def draw_rect(self, r: Rect, color=None, fill=None):
        self.ops.append(("rect", r, color, fill))

    def draw_line(self, p1, p2, color=(0, 0, 0), width=0.6):
        self.ops.append(("ligne", p1, p2, color, width))

    def insert_text(self, pt, texte, fontsize=11, fontname=None):
        self.ops.append(("texte", pt[0], pt[1], texte, fontsize))

    def insert_textbox(self, r: Rect, texte, fontsize=9, fontname=None, align=0) -> float:
        """Texte coupé à la largeur de la zone ; renvoie -1 s'il ne tient pas en hauteur."""
        lignes, courante = [], ""
        for mot in texte.split():
            essai = (courante + " " + mot).strip()
            if stringWidth(essai, POLICE, fontsize) <= r.width or not courante:
                courante = essai
            else:
                lignes.append(courante)
                courante = mot
        if courante:
            lignes.append(courante)
        interligne = fontsize * 1.2
        if len(lignes) * interligne > r.height + 0.5:
            return -1
        for k, l in enumerate(lignes):
            y = r.y0 + fontsize + k * interligne
            if align == 1:
                x = r.x0 + (r.width - stringWidth(l, POLICE, fontsize)) / 2
            else:
                x = r.x0
            self.ops.append(("texte", x, y, l, fontsize))
        return r.height - len(lignes) * interligne

    def insert_image(self, r: Rect, image):
        self.ops.append(("image", r, image))


class Document:
    def __init__(self):
        self.pages: list[Page] = []

    def new_page(self, width: float, height: float) -> Page:
        p = Page(width, height)
        self.pages.append(p)
        return p

    def to_bytes(self) -> bytes:
        tampon = io.BytesIO()
        c = canvas.Canvas(tampon)
        for p in self.pages:
            h = p.hauteur
            c.setPageSize((p.largeur, h))
            for op in p.ops:
                if op[0] == "rect":
                    _, r, color, fill = op
                    if fill is not None:
                        c.setFillColorRGB(*fill)
                    if color is not None:
                        c.setStrokeColorRGB(*color)
                    c.rect(r.x0, h - r.y1, r.width, r.height, stroke=int(color is not None), fill=int(fill is not None))
                elif op[0] == "ligne":
                    _, p1, p2, color, width = op
                    c.setStrokeColorRGB(*color)
                    c.setLineWidth(width)
                    c.line(p1[0], h - p1[1], p2[0], h - p2[1])
                elif op[0] == "texte":
                    _, x, y, texte, taille = op
                    c.setFillColorRGB(0, 0, 0)
                    c.setFont(POLICE, taille)
                    c.drawString(x, h - y, texte)
                elif op[0] == "image":
                    _, r, image = op
                    c.drawImage(ImageReader(image), r.x0, h - r.y1, r.width, r.height)
            c.showPage()
        c.save()
        return tampon.getvalue()

    def save(self, chemin) -> None:
        with open(chemin, "wb") as f:
            f.write(self.to_bytes())

    def rendre(self, index: int = 0, dpi: int = 110, zone: Rect | None = None):
        """Image PIL d'une page (zone en coordonnées haut-gauche)."""
        doc = pdfium.PdfDocument(self.to_bytes())
        page = doc[index]
        w, h = page.get_size()
        crop = (0, 0, 0, 0) if zone is None else (zone.x0, h - zone.y1, w - zone.x1, zone.y0)
        img = page.render(scale=dpi / 72, crop=crop).to_pil().convert("RGB")
        doc.close()
        return img
