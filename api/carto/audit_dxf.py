"""
Audit d'un fichier DXF — À LANCER AVANT TOUT DÉVELOPPEMENT.

Objectif : savoir ce qu'il y a réellement dans le fichier du collègue avant
d'écrire quoi que ce soit de spécifique. Ne suppose rien. Atteste que
l'extraction (blocs + ATTRIB, calques AutoCAD, couleurs, Z, 3D) tient.

Usage (depuis BANCARISATION_COMPENSATION/backend) :
    python3 api/carto/audit_dxf.py /chemin/plan.dxf
    python3 api/carto/audit_dxf.py /chemin/plan.dxf --json rapport.json
    python3 api/carto/audit_dxf.py /chemin/plan.dxf --sans-extraction

    python audit_dxf.py "/Volumes/T7/Travaux_Freelance/KERELIA/CUAs/COMPENSATION_PARCELLE/BANCARISATION_COMPENSATION/backend/api/carto/transfer-01a07b58/19.054 APS_2025.10.17.dxf" --json rapport.json
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List

from ezdxf import recover

try:
    from api.carto.dxf_contexte import (
        TYPES_3D,
        eclater_avec_contexte,
        extraire_calques,
        visible_defaut_calque,
    )
    from api.carto.dxf_processor import ProcesseurDXF
except ImportError:  # exécution CLI : python3 api/carto/audit_dxf.py
    from dxf_contexte import (
        TYPES_3D,
        eclater_avec_contexte,
        extraire_calques,
        visible_defaut_calque,
    )
    from dxf_processor import ProcesseurDXF

logger = logging.getLogger(__name__)

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


def auditer(chemin: str, extraire: bool = True) -> Dict[str, Any]:
    doc, auditor = recover.readfile(chemin)
    if auditor.has_errors:
        logger.warning("%d erreurs récupérées à la lecture", len(auditor.errors))

    header = doc.header
    insunits = header.get("$INSUNITS", 0)
    nom_unite, facteur = INSUNITS.get(insunits, ("inconnu", None))

    msp = doc.modelspace()
    types_niveau0 = Counter(e.dxftype() for e in msp)
    nb_inserts = types_niveau0.get("INSERT", 0)

    table_calques = extraire_calques(doc)
    inserts_n0 = _inventaire_inserts_niveau0(msp)
    definitions_blocs = _definitions_blocs(doc)

    types_eclates = Counter()
    par_calque = defaultdict(Counter)
    par_bloc = defaultdict(Counter)
    extrusions_inversees = 0
    textes: List[Dict[str, Any]] = []
    types_3d: Dict[str, int] = {}
    geom_avec_attributs = 0
    attrib_detaches = 0
    echantillon_attrs: List[Dict[str, Any]] = []

    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    z_non_nuls = 0

    for e, ctx in eclater_avec_contexte(msp, compteur_3d=types_3d):
        t = e.dxftype()
        calque = e.dxf.get("layer", "?") if e.dxf.hasattr("layer") else "?"
        types_eclates[t] += 1
        par_calque[calque][t] += 1
        if ctx.bloc:
            par_bloc[ctx.bloc][t] += 1

        if e.dxf.hasattr("extrusion"):
            extrusion = e.dxf.extrusion
            if extrusion is not None and round(extrusion[2], 6) < 0:
                extrusions_inversees += 1

        if t in ("TEXT", "MTEXT", "ATTRIB"):
            try:
                contenu = e.plain_text() if hasattr(e, "plain_text") else str(e.dxf.get("text", ""))
                if contenu.strip():
                    rec = {"calque": calque, "texte": contenu.strip()[:120], "dxf_type": t}
                    if ctx.bloc:
                        rec["bloc"] = ctx.bloc
                    if t == "ATTRIB" and e.dxf.hasattr("tag"):
                        rec["tag"] = str(e.dxf.tag)
                    textes.append(rec)
            except Exception:
                pass

        if t == "ATTRIB":
            attrib_detaches += 1
        elif ctx.attributs and t not in ("TEXT", "MTEXT", "ATTDEF"):
            geom_avec_attributs += 1
            if len(echantillon_attrs) < 25:
                echantillon_attrs.append({
                    "calque": calque,
                    "dxf_type": t,
                    "bloc": ctx.bloc,
                    "attributs": dict(ctx.attributs),
                })

        for pt in _points_bruts(e):
            x, y = float(pt[0]), float(pt[1])
            xmin, ymin = min(xmin, x), min(ymin, y)
            xmax, ymax = max(xmax, x), max(ymax, y)
            if len(pt) > 2 and abs(float(pt[2])) > 1e-9:
                z_non_nuls += 1

    bbox = None
    crs_candidats = []
    if xmin != float("inf"):
        bbox = {
            "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
            "largeur": xmax - xmin, "hauteur": ymax - ymin,
        }
        crs_candidats = deviner_crs(xmin, ymin, xmax, ymax)

    calques_utilises = {}
    for nom, compteur in sorted(par_calque.items(), key=lambda kv: -sum(kv[1].values())):
        meta = table_calques.get(nom) or {}
        calques_utilises[nom] = {
            "total": sum(compteur.values()),
            "types": dict(compteur),
            "couleur": meta.get("couleur"),
            "eteint": bool(meta.get("eteint")),
            "gele": bool(meta.get("gele")),
            "verrouille": bool(meta.get("verrouille")),
            "visible_defaut": visible_defaut_calque(nom, meta),
        }

    calques_masques_autocad = {
        nom: info for nom, info in calques_utilises.items()
        if info.get("eteint") or info.get("gele")
    }

    blocs_apres = {
        nom: {"total": sum(c.values()), "types": dict(c)}
        for nom, c in sorted(par_bloc.items(), key=lambda kv: -sum(kv[1].values()))
    }

    rapport: Dict[str, Any] = {
        "fichier": chemin,
        "dxf_version": doc.dxfversion,
        "unites": {"insunits": insunits, "nom": nom_unite, "facteur_vers_metre": facteur},
        "erreurs_recuperees": len(auditor.errors),
        "extmin_header": list(header.get("$EXTMIN", (None, None, None))),
        "extmax_header": list(header.get("$EXTMAX", (None, None, None))),
        "modelspace_niveau0": dict(types_niveau0),
        "nb_inserts_niveau0": nb_inserts,
        "inserts_niveau0": inserts_n0,
        "definitions_blocs": definitions_blocs,
        "apres_eclatement": dict(types_eclates),
        "blocs_apres_eclatement": blocs_apres,
        "geometries_avec_attributs": geom_avec_attributs,
        "attrib_encore_en_texte": attrib_detaches,
        "echantillon_attributs_rattaches": echantillon_attrs,
        "nb_calques_table": len(table_calques),
        "nb_calques_utilises": len(calques_utilises),
        "calques": calques_utilises,
        "calques_masques_autocad": calques_masques_autocad,
        "extrusions_inversees": extrusions_inversees,
        "sommets_avec_z": z_non_nuls,
        "types_3d_volumiques": dict(types_3d),
        "bbox_locale": bbox,
        "crs_candidats": crs_candidats,
        "echantillon_textes": textes[:60],
        "extraction": None,
    }

    if extraire:
        rapport["extraction"] = _auditer_extraction(chemin)

    return rapport


def _inventaire_inserts_niveau0(msp) -> Dict[str, Any]:
    par: Dict[str, Dict[str, Any]] = {}
    for e in msp:
        if e.dxftype() != "INSERT":
            continue
        nom = str(e.dxf.get("name", "?")) if e.dxf.hasattr("name") else "?"
        rec = par.setdefault(nom, {"nb": 0, "_tags": set(), "echantillon": []})
        rec["nb"] += 1
        attrs: Dict[str, str] = {}
        for a in getattr(e, "attribs", []) or []:
            try:
                tag = str(a.dxf.get("tag", "") or "").strip() if a.dxf.hasattr("tag") else ""
            except Exception:
                tag = ""
            if not tag:
                continue
            try:
                val = (a.plain_text() if hasattr(a, "plain_text") else str(a.dxf.get("text", ""))) or ""
            except Exception:
                val = ""
            attrs[tag] = val.strip()[:80]
            rec["_tags"].add(tag)
        if attrs and len(rec["echantillon"]) < 5:
            rec["echantillon"].append(attrs)
    sortie = {}
    for nom, rec in sorted(par.items(), key=lambda kv: -kv[1]["nb"]):
        sortie[nom] = {
            "nb": rec["nb"],
            "tags": sorted(rec["_tags"]),
            "echantillon": rec["echantillon"],
        }
    return sortie


def _definitions_blocs(doc) -> List[Dict[str, Any]]:
    defs: List[Dict[str, Any]] = []
    try:
        blocs = doc.blocks
    except Exception:
        return defs
    for block in blocs:
        nom = str(getattr(block, "name", "") or "")
        if not nom or nom.startswith("*"):
            continue
        types = Counter()
        tags: List[str] = []
        try:
            for e in block:
                t = e.dxftype()
                types[t] += 1
                if t == "ATTDEF":
                    try:
                        tag = str(e.dxf.get("tag", "") or "").strip()
                    except Exception:
                        tag = ""
                    if tag:
                        tags.append(tag)
        except Exception:
            continue
        defs.append({
            "nom": nom,
            "nb_entites": sum(types.values()),
            "types": dict(types),
            "tags_attdef": tags,
        })
    defs.sort(key=lambda d: -d["nb_entites"])
    return defs


def _auditer_extraction(chemin: str) -> Dict[str, Any]:
    """Passe par ProcesseurDXF : ce que le pipeline persiste réellement."""
    proc = ProcesseurDXF(chemin, inclure_textes=True, aplatir_z=False)
    if not proc.charger():
        return {"ok": False, "erreur": "lecture impossible"}
    try:
        data = proc.traiter()
    except Exception as exc:
        logger.exception("extraction audit impossible")
        return {"ok": False, "erreur": f"{type(exc).__name__}: {exc}"}

    meta = data.get("metadata") or {}
    features = data.get("features") or []
    nb_couleur = sum(1 for f in features if (f.get("properties") or {}).get("couleur"))
    nb_bloc = sum(1 for f in features if (f.get("properties") or {}).get("bloc"))
    geom_attrs = sum(
        1 for f in features
        if (f.get("properties") or {}).get("attributs")
        and (f.get("properties") or {}).get("dxf_type") not in ("ATTRIB", "TEXT", "MTEXT", "ATTDEF")
    )
    courbes = {
        nom: z for nom, z in (meta.get("profil_z") or {}).items()
        if z.get("probable_courbes_niveau") or z.get("porte_altimetrie")
    }
    return {
        "ok": True,
        "nb_entites": len(features),
        "nb_avec_couleur": nb_couleur,
        "nb_rattachees_a_un_bloc": nb_bloc,
        "nb_geometries_avec_attributs": geom_attrs,
        "nb_entites_avec_attributs": meta.get("nb_entites_avec_attributs") or 0,
        "blocs": meta.get("blocs") or {},
        "profil_z": meta.get("profil_z") or {},
        "calques_alti": courbes,
        "types_3d": meta.get("types_3d") or {},
        "entites_non_converties": meta.get("entites_non_converties") or {},
        "bbox_locale": meta.get("bbox_locale"),
        "echantillon_features": _echantillon_features(features),
    }


def _echantillon_features(features: List[Dict[str, Any]], limite: int = 20) -> List[Dict[str, Any]]:
    choisis: List[Dict[str, Any]] = []
    for f in features:
        props = f.get("properties") or {}
        if not props.get("attributs"):
            continue
        if props.get("dxf_type") in ("ATTRIB", "TEXT", "MTEXT"):
            continue
        choisis.append(_resume_feature(f))
        if len(choisis) >= limite:
            return choisis
    for f in features:
        if len(choisis) >= limite:
            break
        choisis.append(_resume_feature(f))
    return choisis


def _resume_feature(f: Dict[str, Any]) -> Dict[str, Any]:
    props = f.get("properties") or {}
    geom = f.get("geometry") or {}
    return {
        "calque": props.get("calque"),
        "dxf_type": props.get("dxf_type"),
        "bloc": props.get("bloc"),
        "attributs": props.get("attributs") or {},
        "couleur": props.get("couleur"),
        "texte": props.get("texte"),
        "geom": geom.get("type"),
    }


def _points_bruts(e):
    """Points approximatifs d'une entité, en coordonnées d'entité (pour la bbox seulement)."""
    t = e.dxftype()
    try:
        if t == "LINE":
            return [e.dxf.start, e.dxf.end]
        if t == "POINT":
            return [e.dxf.location]
        if t == "INSERT":
            return [e.dxf.insert]
        if t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points(format="xy")]
            try:
                z = float(e.dxf.elevation)
            except Exception:
                z = 0.0
            if abs(z) > 1e-9:
                return [(p[0], p[1], z) for p in pts]
            return pts
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
        if t in TYPES_3D:
            return []
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
        print("  → il FAUT éclater les blocs, sinon on perd l'essentiel.")

    inserts = r.get("inserts_niveau0") or {}
    if inserts:
        print("\n--- INSERT niveau 0 (gisement d'attributs) ---")
        for nom, info in list(inserts.items())[:40]:
            tags = ",".join(info.get("tags") or []) or "sans ATTRIB"
            print(f"  {nom:<32} {info['nb']:>6}  {tags}")
            for ech in (info.get("echantillon") or [])[:2]:
                print(f"      ex. {ech}")

    defs = r.get("definitions_blocs") or []
    defs_avec_tags = [d for d in defs if d.get("tags_attdef")]
    if defs_avec_tags:
        print(f"\n--- Définitions de blocs avec ATTDEF ({len(defs_avec_tags)}) ---")
        for d in defs_avec_tags[:30]:
            print(f"  {d['nom']:<32} {d['nb_entites']:>4} entités  {','.join(d['tags_attdef'])}")

    print(f"\nAprès éclatement    : {r['apres_eclatement']}")
    nb_proxies = r["apres_eclatement"].get("ACAD_PROXY_ENTITY", 0)
    if nb_proxies:
        print(f"  ⚠  {nb_proxies} ACAD_PROXY_ENTITY non éclatés (graphiques proxy illisibles).")

    print(
        f"Géométries avec ATTRIB rattachés : {r['geometries_avec_attributs']}  "
        f"(ATTRIB encore en points texte : {r['attrib_encore_en_texte']})"
    )
    echs = r.get("echantillon_attributs_rattaches") or []
    if echs:
        print("  échantillon (géométrie ← bloc + attributs) :")
        for ech in echs[:12]:
            print(f"    [{ech['calque']}] {ech['dxf_type']} ← {ech.get('bloc')}  {ech.get('attributs')}")

    if r["extrusions_inversees"]:
        print(f"\n⚠  {r['extrusions_inversees']} entités à extrusion inversée → conversion OCS→WCS obligatoire.")
    print(f"Sommets avec Z ≠ 0  : {r['sommets_avec_z']}")
    types_3d = r.get("types_3d_volumiques") or {}
    if types_3d:
        print(f"⚠  Vraie 3D volumique (non convertie, juste comptée) : {types_3d}")
    else:
        print("3D volumique        : aucune (MESH / 3DSOLID / SURFACE absents — attendu sur un plan d'aménagement)")

    print(f"\nBbox locale : {r['bbox_locale']}")
    if r["crs_candidats"]:
        print("CRS plausibles :")
        for c in r["crs_candidats"]:
            print(f"  - EPSG:{c['srid']} ({c['nom']})")
    else:
        print("⚠  Aucun CRS connu ne correspond → coordonnées locales/chantier, calage manuel nécessaire.")

    masques = r.get("calques_masques_autocad") or {}
    print(f"\n--- Calques utilisés ({r['nb_calques_utilises']} / {r['nb_calques_table']} en table) ---")
    if masques:
        print(f"  {len(masques)} calque(s) éteint(s)/gelé(s) AutoCAD → masqués par défaut à l'affichage.")
    for nom, info in list(r["calques"].items())[:40]:
        flags = []
        if info.get("eteint"):
            flags.append("éteint")
        if info.get("gele"):
            flags.append("gelé")
        if not info.get("visible_defaut"):
            flags.append("masqué")
        flag = f"  ({', '.join(flags)})" if flags else ""
        coul = info.get("couleur") or ""
        print(f"  {nom:<40} {info['total']:>6}  {coul:<8}  {info['types']}{flag}")

    if r["echantillon_textes"]:
        print("\n--- Échantillon de textes (indices de sémantique) ---")
        for t in r["echantillon_textes"][:25]:
            bloc = f" ←{t['bloc']}" if t.get("bloc") else ""
            tag = f" @{t['tag']}" if t.get("tag") else ""
            print(f"  [{t['calque']}]{bloc}{tag} {t['texte']}")

    ext = r.get("extraction")
    if ext is None:
        print("\n--- Extraction pipeline : ignorée (--sans-extraction) ---")
        return
    print("\n=== Extraction pipeline (ProcesseurDXF) ===")
    if not ext.get("ok"):
        print(f"  ✗ échec : {ext.get('erreur')}")
        return
    print(f"  entités extraites          : {ext['nb_entites']}")
    print(f"  rattachées à un bloc       : {ext['nb_rattachees_a_un_bloc']}")
    print(f"  géométries + attributs     : {ext['nb_geometries_avec_attributs']}")
    print(f"  avec couleur résolue       : {ext['nb_avec_couleur']}")
    if ext.get("types_3d"):
        print(f"  3D volumique ignorée       : {ext['types_3d']}")
    if ext.get("entites_non_converties"):
        print(f"  non converties             : {ext['entites_non_converties']}")
    blocs = ext.get("blocs") or {}
    if blocs:
        print("  blocs extraits :")
        for nom, info in list(blocs.items())[:30]:
            tags = ",".join(info.get("tags") or []) or "—"
            print(f"    {nom:<32} {info['nb']:>6}  {tags}")
    alti = ext.get("calques_alti") or {}
    if alti:
        print("  calques altimétriques :")
        for nom, z in alti.items():
            genre = "courbes de niveau" if z.get("probable_courbes_niveau") else "Z variable"
            print(
                f"    {nom:<32} {genre}  "
                f"z {z.get('z_min'):.3f}→{z.get('z_max'):.3f}  "
                f"{z.get('niveaux_distincts')} niveaux"
            )
    else:
        print("  calques altimétriques      : aucun (dessin 2D plat, Z inexploitable)")
    echs_f = ext.get("echantillon_features") or []
    if echs_f:
        print("  échantillon extraits :")
        for f in echs_f[:12]:
            attrs = f.get("attributs") or {}
            extra = f"  {attrs}" if attrs else ""
            bloc = f" ←{f.get('bloc')}" if f.get("bloc") else ""
            print(f"    [{f.get('calque')}] {f.get('dxf_type')} {f.get('geom')}{bloc}{extra}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    ap = argparse.ArgumentParser(description="Audit d'un fichier DXF")
    ap.add_argument("fichier")
    ap.add_argument("--json", dest="sortie_json", default=None)
    ap.add_argument(
        "--sans-extraction",
        action="store_true",
        help="Ne pas passer par ProcesseurDXF (audit structurel seulement)",
    )
    args = ap.parse_args()

    rapport = auditer(args.fichier, extraire=not args.sans_extraction)
    afficher(rapport)

    if args.sortie_json:
        with open(args.sortie_json, "w", encoding="utf-8") as f:
            json.dump(rapport, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nRapport JSON écrit : {args.sortie_json}")


if __name__ == "__main__":
    main()
