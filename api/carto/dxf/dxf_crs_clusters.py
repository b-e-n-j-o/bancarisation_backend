"""Détection de CRS multiples dans un même DXF, par clusterisation des coordonnées.

Problème constaté sur 19.054 APS : le fichier contient de la géométrie en
Lambert-93 ET en CC45. Une bbox globale est alors absurde, la détection auto
échoue, et un rognage par quantile supprimerait de la donnée réelle.

Méthode :
  1. un point représentatif par entité
  2. clusterisation par grille (les nuages CRS sont séparés de ~1000 km,
     l'emprise d'un site fait ~1 km : aucune ambiguïté d'échelle)
  3. pour chaque cluster, CRS candidats par test d'enveloppe
  4. recoupement géographique : la bonne combinaison est celle qui fait
     retomber tous les clusters au même endroit sur Terre
  5. ventilation par calque -> srid_source proposé pour plan_cao_calque

pyproj est optionnel : sans lui, les étapes 1-3 et 5 fonctionnent, l'étape 4
(la plus décisive) est désactivée.

Usage :
    python3 api/carto/dxf_crs_clusters.py extraction.geojson
    python3 api/carto/dxf_crs_clusters.py extraction.geojson --json crs.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from pyproj import Transformer
    PYPROJ = True
except ImportError:  # pragma: no cover
    PYPROJ = False


# ------------------------------------------------------------------ enveloppes
# Volontairement larges : on veut TOUS les candidats plausibles, le recoupement
# géographique tranchera. Les zones CC se recouvrent entre elles et avec le L93,
# c'est normal et attendu.

def _enveloppes_cc() -> List[Dict[str, Any]]:
    """RGF93 / CC42 à CC50 : X0 = 1 700 000, Y0 = (zone-41)*1e6 + 200 000."""
    out = []
    for zone in range(42, 51):
        y0 = (zone - 41) * 1_000_000 + 200_000
        out.append({
            "srid": 3900 + zone, "nom": f"RGF93 / CC{zone}",
            "xmin": 1_100_000, "xmax": 2_350_000,
            "ymin": y0 - 450_000, "ymax": y0 + 450_000,
        })
    return out


ENVELOPPES = [
    {"srid": 2154, "nom": "RGF93 / Lambert-93",
     "xmin": 80_000, "xmax": 1_300_000, "ymin": 6_000_000, "ymax": 7_200_000},
    *_enveloppes_cc(),
    {"srid": 27572, "nom": "NTF / Lambert II étendu (ancien)",
     "xmin": 0, "xmax": 1_200_000, "ymin": 1_600_000, "ymax": 2_750_000},
    {"srid": 32630, "nom": "WGS84 / UTM 30N",
     "xmin": 200_000, "xmax": 900_000, "ymin": 4_600_000, "ymax": 5_600_000},
    {"srid": 32631, "nom": "WGS84 / UTM 31N",
     "xmin": 200_000, "xmax": 900_000, "ymin": 4_600_000, "ymax": 5_600_000},
    {"srid": 4326, "nom": "WGS84 degrés",
     "xmin": -6, "xmax": 10, "ymin": 41, "ymax": 52},
]

# France métropolitaine, pour valider qu'une reprojection est plausible
FRANCE = {"lon_min": -5.5, "lon_max": 9.8, "lat_min": 41.0, "lat_max": 51.5}


def candidats_crs(x: float, y: float) -> List[Dict[str, Any]]:
    return [
        {"srid": e["srid"], "nom": e["nom"]}
        for e in ENVELOPPES
        if e["xmin"] <= x <= e["xmax"] and e["ymin"] <= y <= e["ymax"]
    ]


def nom_crs(srid: Optional[int]) -> Optional[str]:
    if srid is None:
        return None
    for e in ENVELOPPES:
        if e["srid"] == srid:
            return e["nom"]
    return f"EPSG:{srid}"


# ------------------------------------------------------------------ clusters

def _point_representatif(feature: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates")
    if coords is None:
        return None
    for c in _coords(coords):
        return float(c[0]), float(c[1])
    return None


def _coords(c):
    if c and isinstance(c[0], (int, float)):
        yield c
    else:
        for sub in c:
            yield from _coords(sub)


def clusteriser(
    features: List[Dict[str, Any]],
    taille_cellule: float = 10_000.0,
    part_min: float = 0.001,
) -> List[Dict[str, Any]]:
    """Regroupe les entités par composantes connexes sur une grille grossière.

    taille_cellule : 10 km par défaut. Bien au-dessus de l'emprise d'un site
    (~1 km) et bien en dessous de l'écart entre deux CRS (~1000 km).
    part_min : en dessous de ce ratio d'entités, un cluster est signalé comme
    suspect (entités égarées) mais JAMAIS supprimé.
    """
    cellules: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    reperes: Dict[int, Tuple[float, float]] = {}

    for i, f in enumerate(features):
        pt = _point_representatif(f)
        if pt is None:
            continue
        reperes[i] = pt
        cellules[(int(math.floor(pt[0] / taille_cellule)),
                  int(math.floor(pt[1] / taille_cellule)))].append(i)

    # composantes connexes (voisinage 8)
    vues: set = set()
    groupes: List[List[Tuple[int, int]]] = []
    for cle in cellules:
        if cle in vues:
            continue
        pile, groupe = [cle], []
        vues.add(cle)
        while pile:
            cx, cy = pile.pop()
            groupe.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    v = (cx + dx, cy + dy)
                    if v in cellules and v not in vues:
                        vues.add(v)
                        pile.append(v)
        groupes.append(groupe)

    total = len(reperes) or 1
    clusters: List[Dict[str, Any]] = []
    for n, groupe in enumerate(groupes):
        idx = [i for cle in groupe for i in cellules[cle]]
        pts = [reperes[i] for i in idx]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)

        calques = Counter(features[i]["properties"].get("calque", "?") for i in idx)
        blocs = Counter(
            features[i]["properties"].get("bloc") for i in idx
            if features[i]["properties"].get("bloc")
        )

        clusters.append({
            "id": n,
            "nb_entites": len(idx),
            "part": len(idx) / total,
            "suspect": len(idx) / total < part_min,
            "bbox": {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)},
            "largeur": max(xs) - min(xs),
            "hauteur": max(ys) - min(ys),
            "centre": [cx, cy],
            "candidats": candidats_crs(cx, cy),
            "calques": dict(calques.most_common(30)),
            "nb_calques": len(calques),
            "blocs": dict(blocs.most_common(10)),
            "_indices": idx,
        })

    clusters.sort(key=lambda c: -c["nb_entites"])
    for rang, c in enumerate(clusters):
        c["id"] = rang
    return clusters


# ------------------------------------------------------- recoupement géographique

def recouper(clusters: List[Dict[str, Any]], tolerance_km: float = 30.0) -> Dict[str, Any]:
    """Cherche l'attribution de CRS qui fait coïncider géographiquement les clusters.

    C'est le test décisif : deux nuages séparés de 1000 km en coordonnées
    projetées mais qui retombent au même endroit une fois chacun lu dans SON
    système, c'est un fichier multi-CRS, pas des entités égarées.
    """
    if not PYPROJ:
        return {"disponible": False,
                "message": "pyproj absent : recoupement géographique désactivé "
                           "(pip install pyproj)."}

    utiles = [c for c in clusters if c["candidats"]]
    if not utiles:
        return {"disponible": True, "conclusion": "aucun_candidat",
                "message": "Aucun cluster ne tombe dans une enveloppe connue : "
                           "coordonnées locales/chantier, calage manuel nécessaire."}

    combos = list(itertools.product(*[c["candidats"] for c in utiles]))
    if len(combos) > 4000:
        combos = combos[:4000]
        logger.warning("trop de combinaisons, troncature à 4000")

    cache: Dict[int, Transformer] = {}

    def vers_wgs84(srid: int, x: float, y: float):
        if srid not in cache:
            cache[srid] = Transformer.from_crs(srid, 4326, always_xy=True)
        lon, lat = cache[srid].transform(x, y)
        return lon, lat

    resultats = []
    for combo in combos:
        pts = []
        ok = True
        for c, cand in zip(utiles, combo):
            try:
                lon, lat = vers_wgs84(cand["srid"], *c["centre"])
            except Exception:
                ok = False
                break
            if not (math.isfinite(lon) and math.isfinite(lat)):
                ok = False
                break
            if not (FRANCE["lon_min"] <= lon <= FRANCE["lon_max"]
                    and FRANCE["lat_min"] <= lat <= FRANCE["lat_max"]):
                ok = False
                break
            pts.append((lon, lat))
        if not ok or not pts:
            continue

        lat0 = sum(p[1] for p in pts) / len(pts)
        lon0 = sum(p[0] for p in pts) / len(pts)
        ecart = max(
            math.hypot((p[1] - lat0) * 111.32,
                       (p[0] - lon0) * 111.32 * math.cos(math.radians(lat0)))
            for p in pts
        )
        resultats.append({
            "ecart_km": ecart,
            "centre_wgs84": [round(lon0, 6), round(lat0, 6)],
            "attribution": [
                {"cluster": c["id"], "srid": cand["srid"], "nom": cand["nom"],
                 "nb_entites": c["nb_entites"], "part": round(c["part"], 4),
                 "wgs84": [round(p[0], 6), round(p[1], 6)]}
                for c, cand, p in zip(utiles, combo, pts)
            ],
        })

    if not resultats:
        return {"disponible": True, "conclusion": "aucune_combinaison_plausible",
                "message": "Aucune combinaison ne place le dessin en France. "
                           "Coordonnées locales probables."}

    resultats.sort(key=lambda r: r["ecart_km"])
    meilleur = resultats[0]
    second = resultats[1] if len(resultats) > 1 else None

    if meilleur["ecart_km"] <= tolerance_km:
        srids = {a["srid"] for a in meilleur["attribution"]}
        conclusion = "multi_crs_confirme" if len(srids) > 1 else "crs_unique"
    else:
        conclusion = "incertain"

    return {
        "disponible": True,
        "conclusion": conclusion,
        "meilleur": meilleur,
        "second": second,
        "marge_km": (second["ecart_km"] - meilleur["ecart_km"]) if second else None,
        "nb_combinaisons_testees": len(resultats),
    }


# ------------------------------------------------------------- ventilation calques

def srid_par_calque(
    features: List[Dict[str, Any]],
    clusters: List[Dict[str, Any]],
    recoupement: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Propose un srid_source par calque, prêt pour plan_cao_calque.

    Un calque réparti sur plusieurs clusters est signalé : c'est une anomalie
    à faire trancher par l'utilisateur, pas à résoudre automatiquement.
    """
    attribution: Dict[int, Dict[str, Any]] = {}
    if recoupement.get("meilleur"):
        for a in recoupement["meilleur"]["attribution"]:
            attribution[a["cluster"]] = a

    par_calque: Dict[str, Counter] = defaultdict(Counter)
    for c in clusters:
        for i in c["_indices"]:
            par_calque[features[i]["properties"].get("calque", "?")][c["id"]] += 1

    sortie = []
    for calque, repartition in sorted(par_calque.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(repartition.values())
        dominant, nb = repartition.most_common(1)[0]
        info = attribution.get(dominant)
        purete = nb / total
        sortie.append({
            "calque": calque,
            "nb_entites": total,
            "cluster_dominant": dominant,
            "purete": round(purete, 4),
            "srid_source": info["srid"] if info else None,
            "crs_nom": info["nom"] if info else None,
            "ambigu": purete < 0.98,
            "repartition": dict(repartition),
            "confiance": ("haute" if info and purete >= 0.98
                          else "a_verifier" if info else "inconnue"),
        })
    return sortie


# --------------------------------------------------------------------- rapport

def analyser(features: List[Dict[str, Any]], taille_cellule: float = 10_000.0) -> Dict[str, Any]:
    clusters = clusteriser(features, taille_cellule)
    recoupement = recouper(clusters)
    calques = srid_par_calque(features, clusters, recoupement)
    return {
        "nb_entites": len(features),
        "nb_clusters": len(clusters),
        "clusters": [{k: v for k, v in c.items() if k != "_indices"} for c in clusters],
        "recoupement": recoupement,
        "srid_par_calque": calques,
        "multi_crs": recoupement.get("conclusion") == "multi_crs_confirme",
    }


def afficher(r: Dict[str, Any]) -> None:
    print(f"\n{r['nb_entites']} entités → {r['nb_clusters']} cluster(s) de coordonnées\n")
    for c in r["clusters"]:
        flag = "  ⚠ SUSPECT (entités égarées ?)" if c["suspect"] else ""
        print(f"--- Cluster {c['id']} : {c['nb_entites']} entités "
              f"({c['part']*100:.2f} %){flag}")
        print(f"    emprise  : {c['largeur']:,.0f} × {c['hauteur']:,.0f} unités")
        print(f"    centre   : {c['centre'][0]:,.1f} / {c['centre'][1]:,.1f}")
        cands = ", ".join(f"{x['srid']} ({x['nom']})" for x in c["candidats"]) or "aucun"
        print(f"    candidats: {cands}")
        print(f"    calques  : {', '.join(list(c['calques'])[:8])}"
              f"{' …' if c['nb_calques'] > 8 else ''}")
        print()

    rec = r["recoupement"]
    print("--- Recoupement géographique ---")
    if not rec.get("disponible"):
        print(f"  {rec['message']}")
    elif rec.get("meilleur"):
        m = rec["meilleur"]
        etiquettes = {
            "multi_crs_confirme": "⚠ FICHIER MULTI-CRS CONFIRMÉ",
            "crs_unique": "✓ CRS unique",
            "incertain": "? incertain, à faire confirmer",
        }
        print(f"  {etiquettes.get(rec['conclusion'], rec['conclusion'])}")
        print(f"  écart résiduel entre clusters : {m['ecart_km']:.2f} km")
        if rec.get("marge_km") is not None:
            print(f"  marge sur la 2e hypothèse    : {rec['marge_km']:.2f} km")
        print(f"  position     : {m['centre_wgs84'][1]:.5f} N, {m['centre_wgs84'][0]:.5f} E")
        for a in m["attribution"]:
            print(f"    cluster {a['cluster']} ({a['part']*100:5.2f} %) → EPSG:{a['srid']}  {a['nom']}")
    else:
        print(f"  {rec.get('message', rec.get('conclusion'))}")

    print("\n--- srid_source proposé par calque ---")
    for c in r["srid_par_calque"][:40]:
        amb = "  ⚠ réparti sur plusieurs clusters" if c["ambigu"] else ""
        srid = f"EPSG:{c['srid_source']}" if c["srid_source"] else "—"
        print(f"  {c['calque']:<34} {c['nb_entites']:>7}  {srid:<12} {c['confiance']}{amb}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    ap = argparse.ArgumentParser(description="Détection de CRS multiples dans un DXF extrait")
    ap.add_argument("geojson", help="sortie de ProcesseurDXF")
    ap.add_argument("--cellule", type=float, default=10_000.0)
    ap.add_argument("--json", dest="sortie", default=None)
    args = ap.parse_args()

    with open(args.geojson, encoding="utf-8") as f:
        data = json.load(f)
    features = data["features"] if isinstance(data, dict) else data

    r = analyser(features, args.cellule)
    afficher(r)

    if args.sortie:
        with open(args.sortie, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        print(f"\nRapport écrit : {args.sortie}")


if __name__ == "__main__":
    main()
