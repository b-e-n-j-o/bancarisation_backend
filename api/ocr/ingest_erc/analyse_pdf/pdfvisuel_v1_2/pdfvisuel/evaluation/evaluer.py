"""Mesure l'efficacité de la détection (module 1) contre une vérité terrain annotée.

Format d'un fichier d'attendus (un par PDF) :
{
  "pdf": "nom.pdf",
  "exhaustif": true,               # toutes les grilles du document sont listées => toute grille
                                   # détectée en plus est un faux positif
  "grilles": [{
     "page": 30, "type_axe": "mois" | "annees",
     "premiere_colonne": "N+16",   # optionnel : distingue deux grilles de même type sur une page
     "lignes": [{
        "libelle_contient": "Inventaire",   # sous-chaîne du libellé de ligne (casse/accents ignorés)
        "role": "entete",                   # optionnel : marque portée par l'en-tête lui-même
        "croix":   [...], "couleur": [...], "bande": [[...], ...]   # colonnes attendues
     }]
  }],
  "images": [{"page": 32, "classe": "document_raster"}],
  "pages_scannees": [1],
  "routage_vision": [1]            # pages qui doivent avoir au moins une zone vision/OCR
}
Identifiants de colonne : numéro du mois (1-12) ; "N", "N+k" (une plage « N+6 à N+10 » se note
"N+6") ; ou l'année ("2027") quand l'axe n'a pas de N+k.

    python -m pdfvisuel.evaluation.evaluer attendus/*.json --pdf-dir corpus/ [-o rapport.md]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from ..m1_detection.geometrie import normaliser
from ..m1_detection.profil import analyser_pdf


def id_colonne(col: dict, type_axe: str):
    if type_axe == "mois":
        return col.get("mois")
    if col.get("etat_zero") and "n_debut" not in col:
        return "E0"
    if "n_debut" in col:
        return "N" if col["n_debut"] == 0 else f"N+{col['n_debut']}"
    return str(col.get("annee_debut"))


def norm_id(x):
    return x if isinstance(x, int) else str(x).replace(" ", "")


def marques_predites(ligne: dict, grille: dict) -> dict[str, set]:
    out = {"croix": set(), "couleur": set(), "bande": set()}
    for m in ligne["marques"]:
        ids = {id_colonne(grille["colonnes"][i], grille["type_axe"]) for i in m["colonnes"]}
        if m["type"] == "croix":
            out["croix"] |= ids
        elif m["type"].startswith("remplissage"):
            out["couleur"] |= ids
        elif m["type"] == "bande":
            out["bande"] |= ids
    return out


def marques_attendues(ligne: dict) -> dict[str, set]:
    bande = ligne.get("bande", [])
    if bande and isinstance(bande[0], list):
        bande = [x for b in bande for x in b]
    return {"croix": {norm_id(x) for x in ligne.get("croix", [])},
            "couleur": {norm_id(x) for x in ligne.get("couleur", [])},
            "bande": {norm_id(x) for x in bande}}


def evaluer_document(att: dict, profil: dict) -> dict:
    pages = {p["page"]: p for p in profil["pages"]}
    res = {"pdf": att["pdf"], "grilles_attendues": 0, "grilles_trouvees": 0, "grilles_en_trop": 0,
           "lignes_attendues": 0, "lignes_trouvees": 0,
           "cellules": defaultdict(lambda: {"vp": 0, "fp": 0, "fn": 0}),
           "images_ok": 0, "images_attendues": 0, "scans_ok": 0, "scans_attendus": 0,
           "routage_ok": 0, "routage_attendu": 0, "echecs": []}
    utilisees = set()
    for ga in att.get("grilles", []):
        res["grilles_attendues"] += 1
        cand = [g for g in pages.get(ga["page"], {"grilles": []})["grilles"]
                if g["type_axe"] == ga["type_axe"] and g["id"] not in utilisees]
        if ga.get("premiere_colonne"):
            cand = [g for g in cand if g["colonnes"] and
                    norm_id(id_colonne(g["colonnes"][0], g["type_axe"])) == norm_id(ga["premiere_colonne"])]
        if not cand:
            res["echecs"].append(f"grille non trouvée : page {ga['page']} {ga['type_axe']}")
            for la in ga["lignes"]:
                res["lignes_attendues"] += 1
                for t, s in marques_attendues(la).items():
                    res["cellules"][t]["fn"] += len(s)
            continue
        g = cand[0]
        utilisees.add(g["id"])
        res["grilles_trouvees"] += 1
        lignes_utilisees = set()
        for la in ga["lignes"]:
            res["lignes_attendues"] += 1
            cible = normaliser(la.get("libelle_contient", ""))
            lp = next((k for k, l in enumerate(g["lignes"]) if k not in lignes_utilisees
                       and (la.get("role", "corps") == l["role"])
                       and cible in normaliser(l["libelle"])), None)
            att_m = marques_attendues(la)
            if lp is None:
                if any(att_m.values()):
                    res["echecs"].append(f"ligne non trouvée : page {ga['page']} « {la.get('libelle_contient')} »")
                    for t, s in att_m.items():
                        res["cellules"][t]["fn"] += len(s)
                else:
                    res["lignes_trouvees"] += 1   # ligne vide attendue, rien de prédit : correct
                continue
            lignes_utilisees.add(lp)
            res["lignes_trouvees"] += 1
            pred = marques_predites(g["lignes"][lp], g)
            for t in ("croix", "couleur", "bande"):
                p, a = {norm_id(x) for x in pred[t]}, att_m[t]
                res["cellules"][t]["vp"] += len(p & a)
                res["cellules"][t]["fp"] += len(p - a)
                res["cellules"][t]["fn"] += len(a - p)
                if p != a:
                    res["echecs"].append(f"page {ga['page']} « {la.get('libelle_contient')} » {t} : "
                                         f"prédit {sorted(map(str, p))} / attendu {sorted(map(str, a))}")
        if att.get("exhaustif"):
            for k, l in enumerate(g["lignes"]):
                if k in lignes_utilisees:
                    continue
                pred = marques_predites(l, g)
                for t in ("croix", "couleur", "bande"):
                    if pred[t]:
                        res["cellules"][t]["fp"] += len(pred[t])
                        res["echecs"].append(f"page {ga['page']} marque {t} dans une ligne non attendue « {l['libelle'][:50]} »")
    if att.get("exhaustif"):
        for p in profil["pages"]:
            for g in p["grilles"]:
                if g["id"] not in utilisees:
                    res["grilles_en_trop"] += 1
                    res["echecs"].append(f"grille en trop : {g['id']} ({g['type_axe']})")
    for ia in att.get("images", []):
        res["images_attendues"] += 1
        if any(i["classe"] == ia["classe"] for i in pages.get(ia["page"], {"images": []})["images"]):
            res["images_ok"] += 1
        else:
            res["echecs"].append(f"image page {ia['page']} : classe {ia['classe']} non trouvée")
    for n in att.get("pages_scannees", []):
        res["scans_attendus"] += 1
        if pages.get(n, {}).get("page_scannee"):
            res["scans_ok"] += 1
        else:
            res["echecs"].append(f"page {n} : scan non détecté")
    for n in att.get("routage_vision", []):
        res["routage_attendu"] += 1
        if any(z["page"] == n and z["action"] in ("vision_zone", "ocr_page") for z in profil["routage"]):
            res["routage_ok"] += 1
        else:
            res["echecs"].append(f"page {n} : aucune zone routée vers la vision")
    res["cellules"] = dict(res["cellules"])
    return res


def _pr(c: dict) -> tuple[float, float]:
    p = c["vp"] / (c["vp"] + c["fp"]) if c["vp"] + c["fp"] else 1.0
    r = c["vp"] / (c["vp"] + c["fn"]) if c["vp"] + c["fn"] else 1.0
    return p, r


def rapport(resultats: list[dict]) -> str:
    lignes = ["# Évaluation de la détection géométrique", "",
              "| Document | Grilles trouvées | En trop | Lignes | Croix P/R | Couleur P/R | Bandes P/R | Images | Scans | Routage | Échecs |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    total = defaultdict(lambda: {"vp": 0, "fp": 0, "fn": 0})
    agg = defaultdict(int)
    for r in resultats:
        cel = {t: r["cellules"].get(t, {"vp": 0, "fp": 0, "fn": 0}) for t in ("croix", "couleur", "bande")}
        for t, c in cel.items():
            for k in c:
                total[t][k] += c[k]
        for k in ("grilles_attendues", "grilles_trouvees", "grilles_en_trop", "lignes_attendues", "lignes_trouvees",
                  "images_ok", "images_attendues", "scans_ok", "scans_attendus", "routage_ok", "routage_attendu"):
            agg[k] += r[k]
        f = lambda c: "—" if c["vp"] + c["fp"] + c["fn"] == 0 else "{:.0%}/{:.0%}".format(*_pr(c))
        lignes.append(f"| {r['pdf']} | {r['grilles_trouvees']}/{r['grilles_attendues']} | {r['grilles_en_trop']} | "
                      f"{r['lignes_trouvees']}/{r['lignes_attendues']} | {f(cel['croix'])} | {f(cel['couleur'])} | "
                      f"{f(cel['bande'])} | {r['images_ok']}/{r['images_attendues']} | {r['scans_ok']}/{r['scans_attendus']} | "
                      f"{r['routage_ok']}/{r['routage_attendu']} | {len(r['echecs'])} |")
    lignes += ["", "## Global", ""]
    lignes.append(f"- Grilles : {agg['grilles_trouvees']}/{agg['grilles_attendues']} trouvées, {agg['grilles_en_trop']} en trop.")
    lignes.append(f"- Lignes : {agg['lignes_trouvees']}/{agg['lignes_attendues']}.")
    for t in ("croix", "couleur", "bande"):
        p, rr = _pr(total[t])
        lignes.append(f"- Cellules « {t} » : précision {p:.1%}, rappel {rr:.1%} "
                      f"(VP {total[t]['vp']}, FP {total[t]['fp']}, FN {total[t]['fn']}).")
    lignes.append(f"- Images classées : {agg['images_ok']}/{agg['images_attendues']} ; scans : "
                  f"{agg['scans_ok']}/{agg['scans_attendus']} ; routage vision : {agg['routage_ok']}/{agg['routage_attendu']}.")
    echecs = [(r["pdf"], e) for r in resultats for e in r["echecs"]]
    lignes += ["", "## Échecs", ""] + ([f"- {d} : {e}" for d, e in echecs] or ["Aucun."])
    return "\n".join(lignes)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("attendus", nargs="+", type=Path)
    ap.add_argument("--pdf-dir", type=Path, action="append", required=True,
                    help="dossier des PDF (option répétable : le premier qui contient le fichier gagne)")
    ap.add_argument("-o", "--sortie", type=Path)
    a = ap.parse_args()
    resultats = []
    for f in sorted(a.attendus):
        att = json.loads(f.read_text())
        chemin = next((d / att["pdf"] for d in a.pdf_dir if (d / att["pdf"]).exists()), None)
        if chemin is None:
            raise SystemExit(f"{att['pdf']} introuvable dans {', '.join(map(str, a.pdf_dir))}")
        profil = analyser_pdf(str(chemin))
        resultats.append(evaluer_document(att, profil))
    texte = rapport(resultats)
    if a.sortie:
        a.sortie.write_text(texte)
    print(texte)


if __name__ == "__main__":
    main()
