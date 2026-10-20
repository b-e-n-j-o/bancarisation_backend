"""Enchaîne les trois modules sur un PDF.

    python -m pdfvisuel.pipeline plan.pdf -o sortie/ [--vision non|dry-run|reel] [--ocr cache.json]

- module 1 (toujours, sans API) : profil.json, grilles.md, routage.md, overlays/
- module 2 (optionnel)          : vision/vision.json, vision/zones/*.png
- module 3 (toujours, sans API) : document_enrichi.md, controles.json, controles.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .m1_detection.profil import analyser_pdf, ecrire_sorties
from .m2_vision.vision import MODELE_OCR, MODELE_VISION, executer
from .m3_assemblage.assemblage import assembler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("-o", "--sortie", type=Path, default=Path("sortie"))
    ap.add_argument("--vision", choices=["non", "dry-run", "reel"], default="dry-run")
    ap.add_argument("--ocr", type=Path, help="texte OCR par page déjà calculé (sinon couche texte native)")
    ap.add_argument("--overlays", choices=["detections", "tout", "aucun"], default="detections")
    ap.add_argument("--tableaux", choices=["remplacer", "accompagner"], default="remplacer")
    a = ap.parse_args()

    profil = analyser_pdf(a.pdf)
    ecrire_sorties(profil, a.sortie, a.overlays)
    print("[module 1]", json.dumps(profil["synthese"], ensure_ascii=False))

    vision_path = None
    if a.vision != "non":
        s = executer(a.sortie / "profil.json", a.vision == "dry-run", False, "normale", MODELE_VISION, MODELE_OCR)
        vision_path = a.sortie / "vision" / "vision.json"
        c = s["cout"]
        prix = "vision n/d" if c.get("usd") is None else f"{c['usd']:.4f} USD"
        ocr = "ocr n/d" if c.get("eur_ocr") is None else f"{c.get('pages_ocr', 0)} p. OCR {c['eur_ocr']:.2f} EUR"
        print(f"[module 2] vision={MODELE_VISION} ocr={MODELE_OCR} {len(s['zones'])} zones, "
              f"{c['appels']} appels API, {c['depuis_cache']} depuis le cache, "
              f"{c.get('tokens_entree', 0)} tok. entrée, {c.get('tokens_sortie', 0)} tok. sortie, "
              f"{prix}, {ocr}"
              + (" (dry-run)" if s["dry_run"] else ""))

    r = assembler(a.sortie / "profil.json", vision_path, a.ocr, a.tableaux)
    print("[module 3]", json.dumps(r, ensure_ascii=False))
    print(f"Voir {a.sortie / 'controles.md'} puis {a.sortie / 'document_enrichi.md'}")


if __name__ == "__main__":
    main()
