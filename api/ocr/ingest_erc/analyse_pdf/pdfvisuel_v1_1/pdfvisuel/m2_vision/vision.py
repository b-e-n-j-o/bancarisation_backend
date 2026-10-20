"""MODULE 2 — Lecture ciblée par vision/OCR des seules zones que le module 1 n'a pas pu lire.

Entrée : profil.json du module 1. Seules les entrées de routage dont l'action est
`vision_zone`, `ocr_page` (et, en option, `lecture_vectorielle_a_verifier` ou toutes les
grilles pour un audit) sont traitées. Le reste du document ne part jamais à l'API.

Pour chaque zone :
1. recadrage de la zone à 200 dpi (marge de 6 pt) -> PNG ;
2. choix du moteur :
   - `ocr`    : Mistral OCR sur l'image (texte et tableaux en image, pages scannées) ;
   - `vision` : modèle multimodal avec un prompt ciblé (grilles, couleurs, croix) et une
                réponse JSON imposée ;
3. cache disque par empreinte (image + prompt + modèle) : relancer ne recoûte rien ;
4. sortie vision.json, réinjectée dans le texte par le module 3.

Usage :
    python -m pdfvisuel.m2_vision.vision sortie/profil.json            # appels réels
    python -m pdfvisuel.m2_vision.vision sortie/profil.json --dry-run  # recadrages + prompts seulement
    options : --verifier-grilles (audit des grilles vectorielles), --priorite-min normale|haute,
              --modele-vision, --modele-ocr

Clé : variable d'environnement MISTRAL_API_KEY. Transport HTTP de la bibliothèque standard
(pas de dépendance au SDK mistralai, dont les versions 1.x/2.x ne sont pas compatibles).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..m1_detection.geometrie import DocumentPDF, Rect

API = "https://api.mistral.ai/v1"
MODELE_VISION = os.environ.get("MISTRAL_VISION_MODEL", "mistral-medium-latest")
MODELE_OCR = os.environ.get("MISTRAL_OCR_MODEL", "mistral-ocr-latest")
DPI = 200
MARGE = 6

PROMPT_GRILLE = """Tu lis un extrait de plan de gestion écologique : une grille temporelle (mois ou années).
L'information peut être portée par du texte, des croix, ou SEULEMENT par la couleur de fond des cellules.
Transcris la grille exactement, sans rien inventer.

Réponds en JSON strict :
{
  "type_axe": "mois" | "annees",
  "colonnes": ["libellé exact de chaque colonne temporelle, de gauche à droite"],
  "lignes": [
    {"libelle": "texte des cellules de gauche (code, action, sous-action)",
     "marques": [{"type": "croix" | "couleur" | "bande",
                  "colonnes": ["libellés des colonnes concernées"],
                  "texte": "texte de la cellule ou null",
                  "couleur": "couleur décrite en mots ou null"}]}
  ],
  "cellules_entete_colorees": ["libellés des colonnes d'en-tête dont le fond est coloré, s'il y en a"],
  "incertitudes": ["ce qui est illisible ou ambigu"]
}
Une cellule fusionnée qui couvre plusieurs colonnes est une "bande" : liste toutes les colonnes couvertes."""

PROMPT_DOCUMENT = """Tu lis une image extraite d'un plan de gestion écologique (tableau ou texte en image).
Transcris fidèlement son contenu en Markdown : tableaux en tableaux Markdown, listes en listes,
symboles de légende décrits entre crochets. N'invente rien ; si un passage est illisible, écris [illisible].

Réponds en JSON strict : {"markdown": "...", "incertitudes": ["..."]}
{contexte}"""


# ---------------------------------------------------------------------------- transport
def _post(chemin: str, charge: dict, cle: str, essais: int = 3) -> dict:
    corps = json.dumps(charge).encode()
    for k in range(essais):
        req = urllib.request.Request(f"{API}/{chemin}", data=corps, method="POST", headers={
            "Authorization": f"Bearer {cle}", "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and k + 1 < essais:
                time.sleep(2 ** k * 2)
                continue
            raise RuntimeError(f"HTTP {e.code} sur /{chemin} : {e.read()[:300]!r}") from e
    raise RuntimeError("échec après plusieurs essais")


def appeler_vision(png: bytes, prompt: str, modele: str, cle: str) -> tuple[dict, dict]:
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    rep = _post("chat/completions", {
        "model": modele,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": url},
        ]}],
    }, cle)
    texte = rep["choices"][0]["message"]["content"]
    return json.loads(texte), rep.get("usage", {})


def appeler_ocr(png: bytes, modele: str, cle: str) -> tuple[dict, dict]:
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    rep = _post("ocr", {"model": modele, "document": {"type": "image_url", "image_url": url}}, cle)
    pages = rep.get("pages", [])
    return {"markdown": "\n\n".join(p.get("markdown", "") for p in pages), "incertitudes": []}, \
        rep.get("usage_info", {})


# ---------------------------------------------------------------------------- tâches
def lister_taches(profil: dict, verifier_grilles: bool, priorite_min: str) -> list[dict]:
    ordre = {"aucune": 0, "normale": 1, "haute": 2}
    taches = []
    for z in profil["routage"]:
        action = z["action"]
        if action in ("vision_zone", "ocr_page"):
            if ordre[z["priorite"]] < ordre[priorite_min]:
                continue
        elif action == "lecture_vectorielle_a_verifier" or (verifier_grilles and action == "lecture_vectorielle"):
            pass
        else:
            continue
        if action == "ocr_page":
            moteur, type_zone = "ocr", "page_scannee"
        elif z.get("type_zone") == "document_raster":
            moteur, type_zone = "ocr", "document_raster"
        else:
            moteur, type_zone = "vision", z.get("type_zone", "grille")
        taches.append({**z, "moteur": moteur, "type_zone": type_zone,
                       "id": z.get("ref") or f"p{z['page']:03d}_{type_zone}"})
    return taches


def recadrer(doc: DocumentPDF, tache: dict) -> bytes:
    zone = None
    if tache["zone"] is not None:
        x0, y0, x1, y1 = tache["zone"]
        zone = Rect(x0 - MARGE, y0 - MARGE, x1 + MARGE, y1 + MARGE)
    tampon = io.BytesIO()
    doc.rendre(tache["page"], zone, DPI).save(tampon, format="PNG")
    return tampon.getvalue()


def prompt_pour(tache: dict) -> str:
    if tache["moteur"] == "vision":
        return PROMPT_GRILLE
    ctx = tache.get("contexte") or ""
    return PROMPT_DOCUMENT.replace("{contexte}", f"Texte qui précède l'image dans le document : « {ctx} »" if ctx else "")


def executer(profil_path: Path, dry_run: bool, verifier_grilles: bool, priorite_min: str,
             modele_vision: str, modele_ocr: str, appel_vision=None, appel_ocr=None) -> dict:
    profil = json.loads(profil_path.read_text())
    dossier = profil_path.parent / "vision"
    (dossier / "zones").mkdir(parents=True, exist_ok=True)
    cache = dossier / "cache"
    cache.mkdir(exist_ok=True)
    appel_vision = appel_vision or appeler_vision
    appel_ocr = appel_ocr or appeler_ocr
    cle = os.environ.get("MISTRAL_API_KEY", "")
    if not dry_run and not cle and appel_vision is appeler_vision:
        raise SystemExit("MISTRAL_API_KEY absente : relancer avec --dry-run ou définir la clé.")

    doc = DocumentPDF(profil["pdf"])
    resultats, cout = [], {"appels": 0, "depuis_cache": 0, "usage": []}
    for t in lister_taches(profil, verifier_grilles, priorite_min):
        png = recadrer(doc, t)
        prompt = prompt_pour(t)
        modele = modele_vision if t["moteur"] == "vision" else modele_ocr
        (dossier / "zones" / f"{t['id']}.png").write_bytes(png)
        (dossier / "zones" / f"{t['id']}.prompt.txt").write_text(f"moteur={t['moteur']} modele={modele}\n\n{prompt}")
        empreinte = hashlib.sha256(png + prompt.encode() + modele.encode()).hexdigest()[:24]
        entree = {"id": t["id"], "page": t["page"], "zone": t["zone"], "type_zone": t["type_zone"],
                  "moteur": t["moteur"], "modele": modele, "empreinte": empreinte,
                  "image": f"zones/{t['id']}.png", "source": "vision"}
        f_cache = cache / f"{empreinte}.json"
        if dry_run:
            entree["statut"] = "non_execute (dry-run)"
        elif f_cache.exists():
            entree.update(json.loads(f_cache.read_text()), statut="cache")
            cout["depuis_cache"] += 1
        else:
            try:
                if t["moteur"] == "vision":
                    contenu, usage = appel_vision(png, prompt, modele, cle)
                else:
                    contenu, usage = appel_ocr(png, modele, cle)
                f_cache.write_text(json.dumps({"contenu": contenu, "usage": usage}, ensure_ascii=False))
                entree.update(contenu=contenu, usage=usage, statut="ok")
                cout["appels"] += 1
                cout["usage"].append(usage)
            except Exception as e:  # une zone en échec ne bloque pas les autres
                entree.update(statut="erreur", erreur=str(e)[:300])
        resultats.append(entree)

    sortie = {"profil": str(profil_path), "pdf": profil["pdf"], "dry_run": dry_run,
              "zones": resultats, "cout": cout}
    (dossier / "vision.json").write_text(json.dumps(sortie, ensure_ascii=False, indent=1))
    return sortie


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profil", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verifier-grilles", action="store_true",
                    help="envoie aussi les grilles vectorielles (audit de la lecture géométrique)")
    ap.add_argument("--priorite-min", choices=["normale", "haute"], default="normale")
    ap.add_argument("--modele-vision", default=MODELE_VISION)
    ap.add_argument("--modele-ocr", default=MODELE_OCR)
    a = ap.parse_args()
    s = executer(a.profil, a.dry_run, a.verifier_grilles, a.priorite_min, a.modele_vision, a.modele_ocr)
    print(f"{len(s['zones'])} zones ; appels {s['cout']['appels']} ; cache {s['cout']['depuis_cache']} ; "
          f"sortie {a.profil.parent / 'vision' / 'vision.json'}")


if __name__ == "__main__":
    main()
