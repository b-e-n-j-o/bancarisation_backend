"""Classification des images raster d'une page (sans API).

Objectif : repérer les zones dont l'information n'existe PAS dans la couche texte
(capture d'écran d'un tableau Excel, grille scannée, page numérisée) pour les router vers
une lecture OCR/vision ciblée, et ignorer les photos et cartes pour l'extraction calendaire.

Mesures sur un rendu basse résolution de la zone (≈ 50 dpi) :
- part_blanc  : proportion de pixels quasi blancs (fond de document) ;
- saturation  : saturation moyenne des pixels non blancs (photos, cartes, orthophotos = élevée) ;
- part_encre  : proportion de pixels sombres (texte, traits) ;
- mots_dessus : mots de la couche texte situés sur l'image (légendes vectorielles, OCR déjà fait).
"""
from __future__ import annotations

import numpy as np
from .geometrie import GeometriePage, Rect, texte_mots

AIRE_MIN = 0.015          # part de la page sous laquelle une image est ignorée (logo, puce)
SEUIL_BLANC_PIX = 232
SEUIL_ENCRE_PIX = 110


def mesurer_zone(geo: GeometriePage, rect: Rect, dpi: int = 50) -> dict:
    arr = np.asarray(geo.rendre(rect, dpi), dtype=np.float32)
    if arr.size == 0:
        return {"part_blanc": 1.0, "saturation": 0.0, "part_encre": 0.0}
    mx, mn = arr.max(axis=2), arr.min(axis=2)
    blanc = (mn >= SEUIL_BLANC_PIX)
    encre = (mx <= SEUIL_ENCRE_PIX)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    non_blanc = ~blanc
    return {
        "part_blanc": round(float(blanc.mean()), 3),
        "saturation": round(float(sat[non_blanc].mean()) if non_blanc.any() else 0.0, 3),
        "part_encre": round(float(encre.mean()), 3),
    }


def classer(mesures: dict, mots_dessus: int) -> tuple[str, str]:
    """(classe, raison). Classes : document_raster | photo_ou_carte | texte_deja_present | vide."""
    if mesures["part_blanc"] > 0.985:
        return "vide", "zone quasi blanche"
    if mots_dessus >= 15:
        return "texte_deja_present", f"{mots_dessus} mots de la couche texte recouvrent l'image"
    if mesures["part_blanc"] >= 0.55 and mesures["saturation"] <= 0.18 and mesures["part_encre"] >= 0.004:
        return "document_raster", "fond blanc dominant, peu de couleur, présence d'encre : texte ou tableau en image"
    return "photo_ou_carte", "couleurs ou tons continus dominants"


def analyser_images(geo: GeometriePage) -> list[dict]:
    aire_page = geo.rect.width * geo.rect.height
    sortie = []
    vus = set()
    for brut in geo.images():
        r = brut & geo.rect
        if r.is_empty:
            continue
        cle = (round(r.x0), round(r.y0), round(r.x1), round(r.y1))
        if cle in vus:
            continue
        vus.add(cle)
        part = r.width * r.height / aire_page
        if part < AIRE_MIN:
            continue
        mesures = mesurer_zone(geo, r)
        mots = [m for m in geo.mots if r.x0 <= m.cx <= r.x1 and r.y0 <= m.cy <= r.y1]
        classe, raison = classer(mesures, len(mots))
        # Contexte : dernière ligne de texte juste au-dessus de l'image (titre, « Proposition de … : »)
        haut_de_page = geo.rect.y0 + 0.08 * geo.rect.height   # en-tête courant du document
        au_dessus = [m for m in geo.mots if r.y0 - 70 <= m.y1 <= r.y0 + 2 and m.y0 > haut_de_page
                     and m.x1 > r.x0 - 150 and m.x0 < r.x1 + 150]
        lignes = geo.lignes_de_mots(au_dessus)
        sortie.append({
            "bbox": [round(v, 1) for v in (r.x0, r.y0, r.x1, r.y1)],
            "part_page": round(part, 3),
            "classe": classe,
            "raison": raison,
            "mesures": mesures,
            "mots_dessus": len(mots),
            "contexte_avant": texte_mots(lignes[-1]) if lignes else "",
        })
    return sortie


def est_page_scannee(geo: GeometriePage, images: list[dict]) -> bool:
    """Peu ou pas de couche texte, et une image couvrant l'essentiel de la page."""
    grande = max((i["part_page"] for i in images), default=0)
    return len(geo.mots) < 25 and grande >= 0.5
