"""
Audit d'un fichier DXF — À LANCER AVANT TOUT DÉVELOPPEMENT.

Objectif : savoir ce qu'il y a réellement dans le fichier du collègue avant
d'écrire quoi que ce soit de spécifique. Ne convertit rien, ne suppose rien.

Usage (depuis BANCARISATION_COMPENSATION/backend) :
    python3 api/carto/audit_dxf.py /chemin/plan.dxf
    python3 api/carto/audit_dxf.py /chemin/plan.dxf --json rapport.json
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from typing import Any, Dict

from ezdxf import recover

try:
    from api.carto.dxf_eclater import eclater_sur
except ImportError:  # exécution CLI : python3 api/carto/audit_dxf.py
    from dxf_eclater import eclater_sur

logger = logging.getLogger(__name__)

# Table $INSUNITS (header DXF)
INSUNITS = {
    0: ("sans_unite", None),
    1: ("pouces", 0.0254),
    2: ("pieds", 0.3048),
    4: ("millimetres", 0.001),
    5: ("centimetres", 0.01),
    6: ("metres", 1.0),
    7: ("kilometres", 1000.0),
    10: ("yards", 0.9144),
}

# Enveloppes grossières pour deviner le CRS depuis la bbox (mètres)
ENVELOPPES_CRS = [
    (2154, "Lambert-93", 100_000, 1_300_000, 6_000_000, 7_200_000),
    (3945, "CC45 (Aquitaine/Occitanie)", 1_200_000, 1_800_000, 3_000_000, 3_400_000),
    (3944, "CC44", 1_200_000, 1_800_000, 2_900_000, 3_300_000),
    (3946, "CC46", 1_200_000, 1_800_000, 3_100_000, 3_500_000),
    (32631, "UTM 31N", 200_000, 900_000, 4_700_000, 5_600_000),
    (4326, "WGS84 degrés", -6, 10, 41, 52),
]


def deviner_crs(xmin: float, ymin: float, xmax: float, ymax: float):
    """Propose des SRID plausibles d'après l'ordre de grandeur des coordonnées."""
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    candidats = []
    for srid, nom, x0, x1, y0, y1 in ENVELOPPES_CRS:
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            candidats.append({"srid": srid, "nom": nom})
    return candidats


def auditer(chemin: str) -> Dict[str, Any]:
    doc, auditor = recover.readfile(chemin)
    if auditor.has_errors:
        logger.warning("%d erreurs récupérées à la lecture", len(auditor.errors))

    header = doc.header
    insunits = header.get("$INSUNITS", 0)
    nom_unite, facteur = INSUNITS.get(insunits, ("inconnu", None))

    msp = doc.modelspace()

    # --- 1. Ce qui est directement dans le modelspace (niveau 0) ---
    types_niveau0 = Counter(e.dxftype() for e in msp)
    nb_inserts = types_niveau0.get("INSERT", 0)

    # --- 2. Ce qu'on obtient APRÈS éclatement des blocs ---
    types_eclates = Counter()
    par_calque = defaultdict(Counter)
    extrusions_inversees = 0
    textes = []

    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    z_non_nuls = 0

    for e in eclater_sur(msp):
        t = e.dxftype()
        calque = e.dxf.get("layer", "?") if e.dxf.hasattr("layer") else "?"
        types_eclates[t] += 1
        par_calque[calque][t] += 1

        # extrusion inversée = géométrie miroir si on ne convertit pas l'OCS
        if e.dxf.hasattr("extrusion"):
            extrusion = e.dxf.extrusion
            if extrusion is not None and round(extrusion[2], 6) < 0:
                extrusions_inversees += 1

        if t in ("TEXT", "MTEXT", "ATTRIB"):
            try:
                contenu = e.plain_text() if hasattr(e, "plain_text") else str(e.dxf.get("text", ""))
                if contenu.strip():
                    textes.append({"calque": calque, "texte": contenu.strip()[:120]})
            except Exception:
                pass

        # bbox approximative à partir des points accessibles
        for pt in _points_bruts(e):
            x, y = float(pt[0]), float(pt[1])
            xmin, ymin = min(xmin, x), min(ymin, y)
            xmax, ymax = max(xmax, x), max(ymax, y)
            if len(pt) > 2 and abs(float(pt[2])) > 1e-9:
                z_non_nuls += 1

    bbox = None
    crs_candidats = []
    if xmin != float("inf"):
        bbox = {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
                "largeur": xmax - xmin, "hauteur": ymax - ymin}
        crs_candidats = deviner_crs(xmin, ymin, xmax, ymax)

    calques = {}
    for nom, compteur in sorted(par_calque.items(), key=lambda kv: -sum(kv[1].values())):
        calques[nom] = {"total": sum(compteur.values()), "types": dict(compteur)}

    return {
        "fichier": chemin,
        "dxf_version": doc.dxfversion,
        "unites": {"insunits": insunits, "nom": nom_unite, "facteur_vers_metre": facteur},
        "erreurs_recuperees": len(auditor.errors),
        "extmin_header": list(header.get("$EXTMIN", (None, None, None))),
        "extmax_header": list(header.get("$EXTMAX", (None, None, None))),
        "modelspace_niveau0": dict(types_niveau0),
        "nb_inserts_niveau0": nb_inserts,
        "apres_eclatement": dict(types_eclates),
        "nb_calques_utilises": len(calques),
        "calques": calques,
        "extrusions_inversees": extrusions_inversees,
        "sommets_avec_z": z_non_nuls,
        "bbox_locale": bbox,
        "crs_candidats": crs_candidats,
        "echantillon_textes": textes[:60],
    }


def _points_bruts(e):
    """Points approximatifs d'une entité, en coordonnées d'entité (pour la bbox seulement)."""
    t = e.dxftype()
    try:
        if t == "LINE":
            return [e.dxf.start, e.dxf.end]
        if t == "POINT":
            return [e.dxf.location]
        if t == "LWPOLYLINE":
            return [(p[0], p[1]) for p in e.get_points(format="xy")]
        if t == "POLYLINE":
            return [v.dxf.location for v in e.vertices]
        if t in ("CIRCLE", "ARC"):
            c, r = e.dxf.center, e.dxf.radius
            return [(c.x - r, c.y - r), (c.x + r, c.y + r)]
        if t in ("TEXT", "ATTRIB"):
            return [e.dxf.insert]
        if t == "MTEXT":
            return [e.dxf.insert]
        if t == "HATCH":
            pts = []
            for chemin in e.paths:
                pts.extend(getattr(chemin, "vertices", []) or [])
            return pts
        if t == "SPLINE":
            return list(e.control_points)
        if t == "ELLIPSE":
            c = e.dxf.center
            mj = e.dxf.major_axis
            return [(c.x - mj.x, c.y - mj.y), (c.x + mj.x, c.y + mj.y)]
        if t in ("SOLID", "TRACE", "3DFACE"):
            return [e.dxf.vtx0, e.dxf.vtx1, e.dxf.vtx2, e.dxf.vtx3]
        if t in ("IMAGE", "WIPEOUT"):
            return [e.dxf.insert]
    except Exception:
        pass
    return []


def afficher(rapport: Dict[str, Any]) -> None:
    r = rapport
    print(f"\n=== {r['fichier']} ===")
    print(f"Version DXF        : {r['dxf_version']}")
    u = r["unites"]
    print(f"Unités ($INSUNITS) : {u['insunits']} → {u['nom']} (×{u['facteur_vers_metre']} pour passer en m)")
    if u["insunits"] == 0:
        print("  ⚠  Unités non déclarées : à confirmer avec le géomètre/l'aménageur.")
    print(f"Erreurs récupérées : {r['erreurs_recuperees']}")

    print(f"\nModelspace niveau 0 : {r['modelspace_niveau0']}")
    print(f"Blocs (INSERT)      : {r['nb_inserts_niveau0']}")
    if r["nb_inserts_niveau0"]:
        print("  → confirme qu'il FAUT éclater les blocs, sinon on perd l'essentiel.")
    print(f"Après éclatement    : {r['apres_eclatement']}")
    nb_proxies = r["apres_eclatement"].get("ACAD_PROXY_ENTITY", 0)
    if nb_proxies:
        print(f"  ⚠  {nb_proxies} ACAD_PROXY_ENTITY non éclatés (graphiques proxy illisibles).")

    if r["extrusions_inversees"]:
        print(f"\n⚠  {r['extrusions_inversees']} entités à extrusion inversée → conversion OCS→WCS obligatoire.")
    print(f"Sommets avec Z ≠ 0  : {r['sommets_avec_z']}")

    print(f"\nBbox locale : {r['bbox_locale']}")
    if r["crs_candidats"]:
        print("CRS plausibles :")
        for c in r["crs_candidats"]:
            print(f"  - EPSG:{c['srid']} ({c['nom']})")
    else:
        print("⚠  Aucun CRS connu ne correspond → coordonnées locales/chantier, calage manuel nécessaire.")

    print(f"\n--- Calques ({r['nb_calques_utilises']}) ---")
    for nom, info in list(r["calques"].items())[:40]:
        print(f"  {nom:<40} {info['total']:>6}  {info['types']}")

    if r["echantillon_textes"]:
        print("\n--- Échantillon de textes (indices de sémantique) ---")
        for t in r["echantillon_textes"][:25]:
            print(f"  [{t['calque']}] {t['texte']}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    ap = argparse.ArgumentParser(description="Audit d'un fichier DXF")
    ap.add_argument("fichier")
    ap.add_argument("--json", dest="sortie_json", default=None)
    args = ap.parse_args()

    rapport = auditer(args.fichier)
    afficher(rapport)

    if args.sortie_json:
        with open(args.sortie_json, "w", encoding="utf-8") as f:
            json.dump(rapport, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nRapport JSON écrit : {args.sortie_json}")


if __name__ == "__main__":
    main()