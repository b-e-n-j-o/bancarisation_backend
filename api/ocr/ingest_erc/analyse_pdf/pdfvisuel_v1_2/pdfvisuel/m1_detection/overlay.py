"""Images de contrôle : la page avec, par-dessus, ce que la détection a compris.

Rendu de la page par pypdfium2, dessin par PIL. Code couleur :
- bleu      : emprise d'une grille temporelle et ses colonnes ;
- rouge     : cellule marquée par une croix ;
- orange    : cellule marquée par une couleur (remplissage) ;
- violet    : bande (cellule fusionnée avec texte) ;
- vert      : image « document_raster » (routée vers la vision) ;
- gris      : photo ou carte (ignorée) ;
- magenta   : zone suspecte (graine temporelle non lue) ou page scannée.
"""
from __future__ import annotations

from PIL import ImageDraw

from .geometrie import DocumentPDF

COULEURS = {
    "grille": (25, 90, 230),
    "croix": (230, 25, 25),
    "remplissage": (255, 140, 0),
    "remplissage_minoritaire": (255, 140, 0),
    "remplissage_uniforme": (255, 205, 0),
    "bande": (140, 50, 190),
    "texte": (100, 100, 100),
    "document_raster": (0, 165, 50),
    "photo_ou_carte": (140, 140, 140),
    "suspect": (230, 0, 180),
}


def dessiner(doc: DocumentPDF, num_page: int, profil_page: dict, chemin_png: str, dpi: int = 80) -> None:
    img = doc.rendre(num_page, None, dpi).convert("RGBA")
    calque = img.copy()
    d = ImageDraw.Draw(calque)
    k = dpi / 72

    def r(b, marge=0.0):
        return [(b[0] - marge) * k, (b[1] - marge) * k, (b[2] + marge) * k, (b[3] + marge) * k]

    def cadre(b, coul, larg=1, remplir=False, marge=0.0):
        d.rectangle(r(b, marge), outline=coul + (255,), width=max(1, int(larg)),
                    fill=coul + (70,) if remplir else None)

    for g in profil_page["grilles"]:
        x0, y0, x1, y1 = g["bbox"]
        cadre(g["bbox"], COULEURS["grille"], 2, marge=2)
        rangees = g.get("rangees", [g["bbox"]])
        for c in g["colonnes"]:
            r_ = rangees[c.get("rangee", 0)]
            cadre((c["x0"], r_[1], c["x1"], r_[3]), COULEURS["grille"], 1)
        for ligne in g["lignes"]:
            for m in ligne["marques"]:
                cols = [g["colonnes"][i] for i in m["colonnes"]]
                coul = COULEURS.get(m["type"], (0, 0, 0))
                ya, yb = m.get("y0", ligne["y0"]), m.get("y1", ligne["y1"])
                cadre((cols[0]["x0"] + 1, ya + 1, cols[-1]["x1"] - 1, yb - 1), coul, 2, True)
        d.text((x0 * k, max(0, (y0 - 12) * k)), f"{g['id']} {g['type_axe']} {g['confiance']}", fill=COULEURS["grille"])

    for im in profil_page["images"]:
        coul = COULEURS.get(im["classe"], (80, 80, 80))
        cadre(im["bbox"], coul, 3 if im["classe"] == "document_raster" else 1)
        d.text((im["bbox"][0] * k + 3, im["bbox"][1] * k + 3), im["classe"], fill=coul)

    for s in profil_page["suspects"]:
        cadre(s["bbox"], COULEURS["suspect"], 3)
        d.text((s["bbox"][0] * k, max(0, s["bbox"][1] * k - 12)), "suspect : " + s["motif"], fill=COULEURS["suspect"])
    if profil_page["page_scannee"]:
        d.rectangle([4, 4, img.width - 5, img.height - 5], outline=COULEURS["suspect"] + (255,), width=4)

    img.alpha_composite(calque)
    img.convert("RGB").save(chemin_png)
