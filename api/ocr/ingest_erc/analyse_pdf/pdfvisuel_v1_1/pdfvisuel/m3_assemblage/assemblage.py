"""MODULE 3 — Assemblage du texte enrichi + contrôles de cohérence (sans API).

Le texte de base (OCR Mistral en cache, ou couche texte native du PDF) est conservé tel quel ;
on ajoute à la fin de chaque page un bloc balisé qui porte ce que le texte ne contient pas :
- les grilles temporelles lues géométriquement (module 1), avec leur lecture en phrases ;
- les transcriptions vision/OCR des zones en image (module 2), marquées source=vision ;
- un marqueur explicite pour toute zone en image NON transcrite (le LLM d'extraction doit
  alors poser la question au BE plutôt que d'inventer).

Le format de page « === page N === » est celui de la passe 0 du pipeline d'ingestion.

Usage :
    python -m pdfvisuel.m3_assemblage.assemblage sortie/profil.json
           [--vision sortie/vision/vision.json] [--ocr cache_ocr.json]
Sorties : document_enrichi.md, controles.json, controles.md (dans le dossier du profil).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ..m1_detection.geometrie import DocumentPDF
from ..m1_detection.serialisation import grille_en_markdown
from .controles import tous_les_controles

DEBUT = "<<<ENRICHISSEMENT page={page}>>>"
FIN = "<<<FIN_ENRICHISSEMENT>>>"


def charger_ocr(chemin: Path | None, n_pages: int) -> list[str] | None:
    """Accepte : liste de str ; liste de dicts {markdown|texte} ; {"pages": [...]} (sortie Mistral OCR)."""
    if not chemin:
        return None
    data = json.loads(chemin.read_text())
    pages = data.get("pages", data) if isinstance(data, dict) else data
    textes = []
    for p in pages:
        textes.append(p if isinstance(p, str) else (p.get("markdown") or p.get("texte") or ""))
    if len(textes) != n_pages:
        raise SystemExit(f"OCR : {len(textes)} pages pour un PDF de {n_pages} pages.")
    return textes


def vision_en_markdown(z: dict) -> str:
    tete = (f"[ZONE {z['id']} — page {z['page']} — {z['type_zone']} — transcrite par {z['moteur']} "
            f"({z['modele']}) — source=vision, à vérifier]")
    if z.get("statut") not in ("ok", "cache"):
        return (f"[ZONE {z['id']} — page {z['page']} — {z['type_zone']} — contenu EN IMAGE NON TRANSCRIT "
                f"({z.get('statut')}). Ne rien déduire de cette zone ; signaler l'information manquante.]")
    c = z.get("contenu", {})
    if "markdown" in c:
        corps = c["markdown"]
    else:  # grille lue par le modèle multimodal
        lignes = [f"Axe : {c.get('type_axe')} ; colonnes : {', '.join(c.get('colonnes', []))}"]
        if c.get("cellules_entete_colorees"):
            lignes.append("Cellules d'en-tête colorées : " + ", ".join(c["cellules_entete_colorees"]))
        for l in c.get("lignes", []):
            ms = "; ".join(f"{m.get('type')} {', '.join(m.get('colonnes', []))}"
                           + (f" « {m['texte']} »" if m.get("texte") else "") for m in l.get("marques", []))
            lignes.append(f"- « {l.get('libelle')} » : {ms or 'aucune marque'}")
        corps = "\n".join(lignes)
    inc = c.get("incertitudes") or []
    return "\n".join([tete, "", corps] + ([f"Incertitudes : {'; '.join(inc)}"] if inc else []))


def assembler(profil_path: Path, vision_path: Path | None, ocr_path: Path | None) -> dict:
    profil = json.loads(profil_path.read_text())
    doc = DocumentPDF(profil["pdf"])
    if vision_path is None and (profil_path.parent / "vision" / "vision.json").exists():
        vision_path = profil_path.parent / "vision" / "vision.json"
    vision = json.loads(vision_path.read_text()) if vision_path else None
    ocr = charger_ocr(ocr_path, len(doc))

    par_page_profil = {p["page"]: p for p in profil["pages"]}
    zones_vision: dict[int, list] = {}
    for z in (vision or {}).get("zones", []):
        zones_vision.setdefault(z["page"], []).append(z)
    ids_vision = {z["id"] for z in (vision or {}).get("zones", [])}

    morceaux, n_blocs = [], 0
    for n in range(1, len(doc) + 1):
        base = ocr[n - 1] if ocr else re.sub(r"[ \t]{3,}", "  ", doc.plumber.pages[n - 1].extract_text() or "")
        base = re.sub(r"\n{3,}", "\n\n", base)
        morceaux.append(f"=== page {n} ===\n{base.rstrip()}")
        ajouts = []
        pp = par_page_profil.get(n)
        if pp:
            ajouts += [grille_en_markdown(g) for g in pp["grilles"]]
        ajouts += [vision_en_markdown(z) for z in zones_vision.get(n, [])]
        # zones routées vers la vision mais absentes de vision.json (module 2 pas lancé)
        for r in profil["routage"]:
            ident = r.get("ref") or f"p{r['page']:03d}_{r.get('type_zone', 'page_scannee')}"
            if r["page"] == n and r["action"] in ("vision_zone", "ocr_page") and ident not in ids_vision:
                ajouts.append(f"[ZONE {ident} — page {n} — {r.get('type_zone', r['action'])} — contenu EN IMAGE "
                              f"NON TRANSCRIT (module 2 non exécuté). Contexte : « {r.get('contexte', '')} ». "
                              f"Ne rien déduire de cette zone ; signaler l'information manquante.]")
        if ajouts:
            n_blocs += 1
            morceaux.append(DEBUT.format(page=n) + "\n" + "\n\n".join(ajouts) + "\n" + FIN)

    controles = tous_les_controles(doc, profil, vision)
    dossier = profil_path.parent
    (dossier / "document_enrichi.md").write_text("\n\n".join(morceaux) + "\n")
    (dossier / "controles.json").write_text(json.dumps(controles, ensure_ascii=False, indent=1))

    ordre = {"alerte": 0, "attention": 1, "info": 2}
    md = [f"# Contrôles — {Path(profil['pdf']).name}", "",
          f"Texte de base : {'OCR fourni' if ocr else 'couche texte native du PDF'} ; "
          f"zones vision : {'oui' if vision else 'non'} ; {n_blocs} pages enrichies.", "",
          "| Niveau | Contrôle | Page | Réf. | Message |", "|---|---|---|---|---|"]
    for c in sorted(controles, key=lambda c: (ordre[c["niveau"]], c["page"])):
        pi = f" ({c['page_imprimee']})" if c.get("page_imprimee") else ""
        md.append(f"| {c['niveau']} | {c['controle']} | {c['page']}{pi} | {c['ref']} | {c['message'].replace('|', '/')} |")
    (dossier / "controles.md").write_text("\n".join(md) + "\n")
    return {"pages_enrichies": n_blocs, "controles": len(controles),
            "alertes": sum(c["niveau"] == "alerte" for c in controles)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profil", type=Path)
    ap.add_argument("--vision", type=Path)
    ap.add_argument("--ocr", type=Path, help="JSON du texte OCR par page (sinon couche texte native)")
    a = ap.parse_args()
    print(json.dumps(assembler(a.profil, a.vision, a.ocr), ensure_ascii=False))


if __name__ == "__main__":
    main()
