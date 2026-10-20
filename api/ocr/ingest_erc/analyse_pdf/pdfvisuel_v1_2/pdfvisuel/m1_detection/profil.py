"""MODULE 1 — Profil visuel d'un PDF, sans aucun appel API.

Pour chaque page : grilles temporelles lues géométriquement, images classées, pages
scannées, zones suspectes ; puis une table de routage qui dit, zone par zone, ce qui est
déjà lu (vecteur), ce qui doit partir en vision/OCR ciblée (module 2) et ce qui est ignoré.

Usage :
    python -m pdfvisuel.m1_detection.profil plan.pdf -o sortie/ [--overlays detections|tout|aucun]
                                                           [--pages 20-45]
Sorties dans le dossier :
    profil.json   — tout le détail (grilles, cellules, images, routage)
    grilles.md    — grilles sérialisées en Markdown + lecture en phrases
    routage.md    — résumé lisible : quoi faire de chaque zone
    overlays/     — pages annotées pour vérifier la détection à l'œil
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from .geometrie import DocumentPDF, GeometriePage, normaliser
from .contexte import ajouter_contexte, fusionner_frises, lignes_repetees, titres_de_page
from .grilles import detecter_grilles
from .images import analyser_images, est_page_scannee
from .overlay import dessiner
from .serialisation import grille_en_markdown

VERSION = "1.2"
RE_CALENDRIER = re.compile(
    r"\b(calendrier|planning|programmation|phasage|periode|echeancier|chronogramme|frise|"
    r"intervention|n\+\d+|janvier|fevrier|mars|avril|juin|juillet|aout|septembre|octobre|novembre|decembre)\b")


def page_imprimee(geo: GeometriePage) -> int | None:
    """Numéro imprimé en pied (ou tête) de page, s'il existe."""
    h = geo.rect.height
    cand = []
    for ligne in geo.lignes_de_mots():
        if len(ligne) == 1 and ligne[0].texte.isdigit() and 0 < int(ligne[0].texte) < 3000:
            m = ligne[0]
            if m.y0 > geo.rect.y0 + 0.80 * h or m.y1 < geo.rect.y0 + 0.08 * h:
                cand.append(m)
    if not cand:
        return None
    return int(max(cand, key=lambda m: m.y0).texte)


def profiler_page(geo: GeometriePage, num: int, titres_precedents: list[dict] | None = None,
                  repetees: set[str] = frozenset()) -> dict:
    grilles, graines = detecter_grilles(geo, num)
    grilles = fusionner_frises(grilles)
    ajouter_contexte(geo, grilles, titres_precedents or [], repetees)
    images = analyser_images(geo)
    scannee = est_page_scannee(geo, images)

    # Graine temporelle qu'aucune grille ne couvre : en-tête repéré mais structure illisible
    suspects = []
    for g in graines:
        cx, cy = (g.x0 + g.x1) / 2, (g.y0 + g.y1) / 2
        couverte = any(gr["bbox"][0] - 5 <= cx <= gr["bbox"][2] + 5 and gr["bbox"][1] - 5 <= cy <= gr["bbox"][3] + 5
                       for gr in grilles)
        if not couverte:
            suspects.append({
                "bbox": [round(g.x0 - 10, 1), round(g.y0 - 10, 1), round(g.x1 + 10, 1), round(min(geo.rect.y1, g.y1 + 200), 1)],
                "motif": f"en-tête {g.type_axe} repéré ({len(g.mots)} libellés) mais grille non reconstruite",
            })
    texte_page = normaliser(" ".join(m.texte for m in geo.mots))
    for im in images:
        x0, y0, x1, y1 = im["bbox"]
        proches = [m.texte for m in geo.mots if y0 - 120 <= m.cy <= y1 + 60]
        im["vocabulaire_calendaire_proche"] = bool(RE_CALENDRIER.search(normaliser(" ".join(proches))))
    return {
        "page": num,
        "page_imprimee": page_imprimee(geo),
        "dimensions": [round(geo.rect.width, 1), round(geo.rect.height, 1)],
        "n_mots": len(geo.mots),
        "n_traits": len(geo.verticaux) + len(geo.horizontaux),
        "n_aplats": len(geo.aplats),
        "page_scannee": scannee,
        "vocabulaire_calendaire": bool(RE_CALENDRIER.search(texte_page)),
        "grilles": grilles,
        "images": images,
        "suspects": suspects,
    }


def construire_routage(pages: list[dict]) -> list[dict]:
    routage = []
    for p in pages:
        n = p["page"]
        if p["page_scannee"]:
            routage.append({"page": n, "zone": None, "action": "ocr_page", "priorite": "haute",
                            "motif": "page numérisée : pas de couche texte exploitable"})
            continue
        for g in p["grilles"]:
            action = "lecture_vectorielle" if g["confiance"] == "haute" else "lecture_vectorielle_a_verifier"
            routage.append({"page": n, "zone": g["bbox"], "action": action, "ref": g["id"],
                            "priorite": "aucune" if action == "lecture_vectorielle" else "normale",
                            "motif": f"grille {g['type_axe']} lue dans le PDF ({g['mode_colonnes']}/{g['mode_lignes']})"})
        for s in p["suspects"]:
            routage.append({"page": n, "zone": s["bbox"], "action": "vision_zone", "priorite": "haute",
                            "type_zone": "grille_suspecte", "motif": s["motif"]})
        for k, im in enumerate(p["images"]):
            if im["classe"] == "document_raster":
                prio = "haute" if im.get("vocabulaire_calendaire_proche") else "normale"
                routage.append({"page": n, "zone": im["bbox"], "action": "vision_zone", "priorite": prio,
                                "type_zone": "document_raster", "ref": f"p{n:03d}_img{k + 1}",
                                "contexte": im["contexte_avant"], "motif": im["raison"]})
            else:
                routage.append({"page": n, "zone": im["bbox"], "action": "ignorer", "priorite": "aucune",
                                "ref": f"p{n:03d}_img{k + 1}", "motif": f"{im['classe']} : {im['raison']}"})
    return routage


def analyser_pdf(chemin: str, pages: list[int] | None = None) -> dict:
    doc = DocumentPDF(chemin)
    debut = time.time()
    nums = pages or list(range(1, len(doc) + 1))
    geos = {n: doc.page(n) for n in nums}
    repetees = lignes_repetees([[" ".join(m.texte for m in l) for l in g.lignes_de_mots()] for g in geos.values()])
    detail, titres = [], []
    for n in nums:
        geo = geos[n]
        # titres d'action des 2 pages précédentes : rattachement d'une grille en haut de page
        recents = [t for t in titres if t["page"] >= n - 2]
        detail.append(profiler_page(geo, n, recents, repetees))
        titres = recents + [dict(t, page=n) for t in titres_de_page(geo, repetees)]
    routage = construire_routage(detail)
    sha = hashlib.sha256(Path(chemin).read_bytes()).hexdigest()
    synthese = {
        "pages_analysees": len(nums),
        "grilles": sum(len(p["grilles"]) for p in detail),
        "grilles_mois": sum(1 for p in detail for g in p["grilles"] if g["type_axe"] == "mois"),
        "grilles_annees": sum(1 for p in detail for g in p["grilles"] if g["type_axe"] == "annees"),
        "marques_par_couleur": sum(1 for p in detail for g in p["grilles"] for l in g["lignes"]
                                   for m in l["marques"] if m["type"].startswith("remplissage")),
        "images_document_raster": sum(1 for p in detail for i in p["images"] if i["classe"] == "document_raster"),
        "pages_scannees": sum(p["page_scannee"] for p in detail),
        "zones_suspectes": sum(len(p["suspects"]) for p in detail),
        "zones_vers_vision": sum(1 for r in routage if r["action"] in ("vision_zone", "ocr_page")),
        "duree_s": round(time.time() - debut, 2),
    }
    return {"version": VERSION, "pdf": str(chemin), "sha256": sha, "n_pages_pdf": len(doc),
            "synthese": synthese, "pages": detail, "routage": routage}


def ecrire_sorties(profil: dict, dossier: Path, overlays: str = "detections") -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "profil.json").write_text(json.dumps(profil, ensure_ascii=False, indent=1))

    md = [f"# Grilles temporelles lues dans {Path(profil['pdf']).name}", ""]
    for p in profil["pages"]:
        for g in p["grilles"]:
            md += [f"## Page {p['page']}" + (f" (imprimée {p['page_imprimee']})" if p["page_imprimee"] else ""),
                   "", grille_en_markdown(g), ""]
    (dossier / "grilles.md").write_text("\n".join(md))

    s = profil["synthese"]
    r = [f"# Routage — {Path(profil['pdf']).name}", "",
         f"{s['pages_analysees']} pages, {s['grilles']} grilles lues ({s['grilles_mois']} mensuelles, "
         f"{s['grilles_annees']} annuelles), {s['marques_par_couleur']} marques portées uniquement par la couleur, "
         f"{s['zones_vers_vision']} zones à envoyer en vision/OCR, durée {s['duree_s']} s.", "",
         "| Page | Action | Priorité | Référence | Motif |", "|---|---|---|---|---|"]
    imprimees = {p["page"]: p["page_imprimee"] for p in profil["pages"]}
    for z in profil["routage"]:
        if z["action"] == "ignorer":
            continue
        pi = imprimees.get(z["page"])
        r.append(f"| {z['page']}{f' ({pi})' if pi else ''} | {z['action']} | {z['priorite']} | "
                 f"{z.get('ref', '')} | {z['motif']} |")
    ignorees = sum(1 for z in profil["routage"] if z["action"] == "ignorer")
    r += ["", f"{ignorees} images ignorées (photos, cartes) : détail dans profil.json."]
    (dossier / "routage.md").write_text("\n".join(r))

    if overlays != "aucun":
        od = dossier / "overlays"
        od.mkdir(exist_ok=True)
        doc = DocumentPDF(profil["pdf"])
        for p in profil["pages"]:
            if overlays == "tout" or p["grilles"] or p["suspects"] or p["page_scannee"] or \
                    any(i["classe"] == "document_raster" for i in p["images"]):
                dessiner(doc, p["page"], p, str(od / f"page_{p['page']:03d}.png"))


def _pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("-o", "--sortie", default="sortie_profil")
    ap.add_argument("--overlays", choices=["detections", "tout", "aucun"], default="detections")
    ap.add_argument("--pages", help="ex. 20-45 ou 3,7,10-12 (numéros PDF, base 1)")
    a = ap.parse_args()
    profil = analyser_pdf(a.pdf, _pages(a.pages))
    ecrire_sorties(profil, Path(a.sortie), a.overlays)
    print(json.dumps(profil["synthese"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
