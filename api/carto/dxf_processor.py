"""
Extraction des géométries d'un DXF vers GeoJSON, en coordonnées CAO brutes.

  - éclate les blocs (INSERT) récursivement, en conservant nom + ATTRIB
  - convertit OCS → WCS (make_path / ocs.to_wcs)
  - gère bulges, ARC, ELLIPSE, SPLINE, HATCH via ezdxf.path
  - lit $INSUNITS et normalise en mètres si déclaré
  - lecture tolérante (ezdxf.recover)
  - résout couleurs (true_color / ACI / ByLayer / ByBlock)
  - ne prétend PAS connaître le CRS : sortie en coordonnées locales

Le calage (translation/rotation/échelle vers le SRID cible) est fait EN AVAL.

Usage (depuis BANCARISATION_COMPENSATION/backend) :
    python3 api/carto/dxf_processor.py plan.dxf -o plan.geojson
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

from ezdxf import recover
from ezdxf.document import Drawing
from ezdxf.path import make_path

try:
    from api.carto.dxf_contexte import (
        TYPES_3D,
        Contexte,
        couleur_entite,
        eclater_avec_contexte,
        extraire_calques,
        inventaire_blocs,
        profil_z_par_calque,
        visible_defaut_calque,
    )
except ImportError:  # exécution CLI : python3 api/carto/dxf_processor.py
    from dxf_contexte import (
        TYPES_3D,
        Contexte,
        couleur_entite,
        eclater_avec_contexte,
        extraire_calques,
        inventaire_blocs,
        profil_z_par_calque,
        visible_defaut_calque,
    )

logger = logging.getLogger(__name__)

INSUNITS_VERS_METRE = {0: None, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 7: 1000.0, 10: 0.9144}

# Flèche max autorisée lors de la discrétisation des courbes, en mètres de dessin.
FLECHE_METRES = 0.01

TYPES_LINEAIRES = {
    "LINE", "LWPOLYLINE", "POLYLINE", "ARC", "ELLIPSE", "SPLINE",
    "SOLID", "TRACE", "3DFACE", "CIRCLE",
}
TYPES_TEXTE = {"TEXT", "MTEXT", "ATTRIB"}
TYPES_RASTER = {
    "IMAGE", "PDFREFERENCE", "PDFUNDERLAY", "DWFUNDERLAY", "DGNUNDERLAY", "WIPEOUT",
}


def _dxf_attr(entite, nom: str, defaut=None):
    if entite.dxf.hasattr(nom):
        return entite.dxf.get(nom, defaut)
    return defaut


def _calque_de(entite) -> str:
    val = _dxf_attr(entite, "layer", "0")
    return str(val) if val is not None else "0"


class ProcesseurDXF:
    """Extrait les entités d'un DXF en GeoJSON, en coordonnées du dessin."""

    def __init__(
        self,
        chemin: str,
        calques_ignores: Optional[Iterable[str]] = None,
        inclure_textes: bool = True,
        normaliser_en_metres: bool = True,
        fleche_metres: float = FLECHE_METRES,
        aplatir_z: bool = False,
        arrondi: Optional[int] = None,
    ):
        self.chemin = chemin
        self.doc: Optional[Drawing] = None
        self.calques_ignores = {c.upper() for c in (calques_ignores or [])}
        self.inclure_textes = inclure_textes
        self.normaliser_en_metres = normaliser_en_metres
        self.fleche_metres = fleche_metres
        self.aplatir_z = aplatir_z
        self.arrondi = arrondi

        self.facteur = 1.0
        self.insunits = 0
        self.features: List[Dict[str, Any]] = []
        self.ignorees: Dict[str, int] = {}
        self.calques_masques: Dict[str, int] = {}
        self.table_calques: Dict[str, Dict[str, Any]] = {}
        self.types_3d: Dict[str, int] = {}
        self.profil_z: Dict[str, Dict[str, Any]] = {}

    def charger(self) -> bool:
        try:
            self.doc, auditor = recover.readfile(self.chemin)
        except OSError:
            logger.error("Lecture impossible : %s", self.chemin)
            return False
        except Exception as exc:
            logger.error("DXF illisible (%s) : %s", type(exc).__name__, exc)
            return False

        if auditor.has_errors:
            logger.warning("%d erreurs DXF récupérées automatiquement", len(auditor.errors))

        self.insunits = self.doc.header.get("$INSUNITS", 0)
        f = INSUNITS_VERS_METRE.get(self.insunits)
        if self.normaliser_en_metres and f:
            self.facteur = f
        elif self.normaliser_en_metres and not f:
            logger.warning(
                "$INSUNITS=%s : unités non déclarées, aucun facteur appliqué. "
                "À confirmer avec l'émetteur du fichier.", self.insunits,
            )
        logger.info(
            "DXF %s chargé, unités=%s, facteur=%s",
            self.doc.dxfversion, self.insunits, self.facteur,
        )
        return True

    def traiter(self) -> Dict[str, Any]:
        if not self.doc:
            raise ValueError("Appeler charger() avant traiter().")

        self.features = []
        self.ignorees = {}
        self.calques_masques = {}
        self.types_3d = {}
        self.table_calques = extraire_calques(self.doc)
        fleche = self.fleche_metres / self.facteur if self.facteur else self.fleche_metres

        for entite, ctx in eclater_avec_contexte(
            self.doc.modelspace(), compteur_3d=self.types_3d,
        ):
            t = entite.dxftype()
            calque = _calque_de(entite)
            if calque.upper() in self.calques_ignores:
                self.calques_masques[calque] = self.calques_masques.get(calque, 0) + 1
                continue

            try:
                if t in TYPES_3D:
                    self.ignorees[t] = self.ignorees.get(t, 0) + 1
                    continue
                if t in TYPES_LINEAIRES:
                    feat = self._depuis_chemin(entite, fleche, calque, ctx)
                    if feat:
                        self.features.append(feat)
                elif t == "HATCH":
                    self.features.extend(self._depuis_hachure(entite, fleche, calque, ctx))
                elif t == "POINT":
                    ocs = entite.ocs()
                    p = ocs.to_wcs(entite.dxf.location)
                    self.features.append(
                        self._feature("Point", self._pt(p), entite, calque, ctx)
                    )
                elif t == "INSERT":
                    feat = self._depuis_insert(entite, calque, ctx)
                    if feat:
                        self.features.append(feat)
                elif t in TYPES_TEXTE and self.inclure_textes:
                    feat = self._depuis_texte(entite, calque, ctx)
                    if feat:
                        self.features.append(feat)
                elif t in TYPES_RASTER:
                    continue
                else:
                    self.ignorees[t] = self.ignorees.get(t, 0) + 1
            except Exception as exc:
                logger.debug("Entité %s (%s) ignorée : %s", t, _dxf_attr(entite, "handle"), exc)
                self.ignorees[f"{t}(erreur)"] = self.ignorees.get(f"{t}(erreur)", 0) + 1

        for i, feat in enumerate(self.features):
            feat["id"] = i

        self.profil_z = profil_z_par_calque(self.features)
        blocs = inventaire_blocs(self.features)
        nb_avec_attrs = sum(
            1 for f in self.features
            if (f.get("properties") or {}).get("attributs")
        )

        if self.types_3d:
            logger.info("Objets 3D volumiques (non convertis) : %s", self.types_3d)
        if self.ignorees:
            logger.info("Entités non converties : %s", self.ignorees)
        logger.info("%d entités extraites, %d avec attributs de bloc", len(self.features), nb_avec_attrs)

        return {
            "type": "FeatureCollection",
            "features": self.features,
            "metadata": {
                "source": self.chemin,
                "dxf_version": self.doc.dxfversion,
                "insunits": self.insunits,
                "facteur_applique": self.facteur,
                "unite_sortie": "metre" if self.facteur != 1.0 or self.insunits == 6 else "unite_dessin",
                "entites_non_converties": self.ignorees,
                "types_3d": dict(self.types_3d),
                "bbox_locale": self.bbox(),
                "profil_z": self.profil_z,
                "blocs": blocs,
                "nb_entites_avec_attributs": nb_avec_attrs,
                "calques_table": self.table_calques,
            },
        }

    def _depuis_chemin(
        self, entite, fleche: float, calque: str, ctx: Contexte,
    ) -> Optional[Dict[str, Any]]:
        """make_path() rend un chemin déjà en WCS, bulges et courbes compris."""
        chemin = make_path(entite)
        sommets = [self._pt(p) for p in chemin.flattening(fleche)]
        sommets = _dedoublonner(sommets)
        if len(sommets) < 2:
            return None

        ferme = chemin.is_closed or _memes_xy(sommets[0], sommets[-1])

        if ferme and len(sommets) >= 3:
            anneau = sommets if _memes_xy(sommets[0], sommets[-1]) else sommets + [sommets[0]]
            if len(anneau) < 4:
                return None
            return self._feature("Polygon", [anneau], entite, calque, ctx)
        return self._feature("LineString", sommets, entite, calque, ctx)

    def _depuis_hachure(
        self, entite, fleche: float, calque: str, ctx: Contexte,
    ) -> List[Dict[str, Any]]:
        from ezdxf.path import from_hatch

        sorties = []
        for chemin in from_hatch(entite):
            sommets = _dedoublonner([self._pt(p) for p in chemin.flattening(fleche)])
            if len(sommets) < 3:
                continue
            anneau = sommets if _memes_xy(sommets[0], sommets[-1]) else sommets + [sommets[0]]
            if len(anneau) < 4:
                continue
            sorties.append(self._feature("Polygon", [anneau], entite, calque, ctx))
        return sorties

    def _depuis_insert(self, entite, calque: str, ctx: Contexte) -> Optional[Dict[str, Any]]:
        """INSERT sans géométrie de symbole : point d'insertion + attributs."""
        try:
            loc = entite.dxf.insert
            p = entite.ocs().to_wcs(loc)
        except Exception:
            return None
        return self._feature("Point", self._pt(p), entite, calque, ctx)

    def _depuis_texte(self, entite, calque: str, ctx: Contexte) -> Optional[Dict[str, Any]]:
        t = entite.dxftype()
        if t == "MTEXT":
            point = entite.dxf.insert
            contenu = entite.plain_text()
        else:
            point = entite.ocs().to_wcs(entite.dxf.insert)
            contenu = entite.plain_text() if hasattr(entite, "plain_text") else _dxf_attr(entite, "text", "")
        contenu = (contenu or "").strip()
        if not contenu:
            return None
        feat = self._feature("Point", self._pt(point), entite, calque, ctx)
        feat["properties"]["texte"] = contenu
        if t == "ATTRIB":
            tag = _dxf_attr(entite, "tag", "")
            if tag:
                feat["properties"]["attrib_tag"] = str(tag)
        return feat

    def _pt(self, p) -> List[float]:
        x, y = float(p[0]) * self.facteur, float(p[1]) * self.facteur
        if self.arrondi is not None:
            x, y = round(x, self.arrondi), round(y, self.arrondi)
        if self.aplatir_z:
            return [x, y]
        z = float(p[2]) * self.facteur if len(p) > 2 else 0.0
        if self.arrondi is not None:
            z = round(z, self.arrondi)
        return [x, y, z]

    def _feature(
        self, type_geom: str, coords: Any, entite, calque: str, ctx: Optional[Contexte] = None,
    ) -> Dict[str, Any]:
        ctx = ctx or Contexte()
        props: Dict[str, Any] = {
            "calque": calque,
            "dxf_type": entite.dxftype(),
            "handle": _dxf_attr(entite, "handle"),
            "couleur": couleur_entite(entite, ctx, self.table_calques),
        }
        if ctx.bloc:
            props["bloc"] = ctx.bloc
        if ctx.chemin_blocs:
            props["chemin_blocs"] = list(ctx.chemin_blocs)
        if ctx.attributs:
            props["attributs"] = dict(ctx.attributs)
        return {
            "type": "Feature",
            "geometry": {"type": type_geom, "coordinates": coords},
            "properties": props,
        }

    def bbox(self) -> Optional[Dict[str, float]]:
        xs, ys = [], []
        for f in self.features:
            for x, y, *_ in _parcourir_coords(f["geometry"]["coordinates"]):
                xs.append(x)
                ys.append(y)
        if not xs:
            return None
        return {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)}

    def resume_calques(self) -> Dict[str, int]:
        resume: Dict[str, int] = {}
        for f in self.features:
            c = f["properties"]["calque"]
            resume[c] = resume.get(c, 0) + 1
        return dict(sorted(resume.items(), key=lambda kv: -kv[1]))

    def resume_calques_detail(self) -> List[Dict[str, Any]]:
        par: Dict[str, Counter] = defaultdict(Counter)
        for f in self.features:
            props = f["properties"]
            par[props["calque"]][props["dxf_type"]] += 1
        sorties = []
        for nom, types in sorted(par.items(), key=lambda kv: -sum(kv[1].values())):
            meta = self.table_calques.get(nom) or {}
            zinfo = self.profil_z.get(nom) or {}
            sorties.append({
                "nom": nom,
                "nb": sum(types.values()),
                "types": dict(types),
                "visible_defaut": visible_defaut_calque(nom, meta),
                "couleur": meta.get("couleur"),
                "aci": meta.get("aci"),
                "eteint": bool(meta.get("eteint")),
                "gele": bool(meta.get("gele")),
                "verrouille": bool(meta.get("verrouille")),
                "porte_altimetrie": bool(zinfo.get("porte_altimetrie")),
                "z_min": zinfo.get("z_min"),
                "z_max": zinfo.get("z_max"),
                "probable_courbes_niveau": bool(zinfo.get("probable_courbes_niveau")),
            })
        return sorties


def _dedoublonner(points: List[List[float]], eps: float = 1e-9) -> List[List[float]]:
    sortie: List[List[float]] = []
    for p in points:
        if not sortie or abs(p[0] - sortie[-1][0]) > eps or abs(p[1] - sortie[-1][1]) > eps:
            sortie.append(p)
    return sortie


def _memes_xy(a, b, eps: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def _parcourir_coords(coords):
    if coords and isinstance(coords[0], (int, float)):
        yield coords
    else:
        for c in coords:
            yield from _parcourir_coords(c)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    ap = argparse.ArgumentParser(description="DXF → GeoJSON (coordonnées du dessin)")
    ap.add_argument("fichier")
    ap.add_argument("-o", "--sortie", default="extraction.geojson")
    ap.add_argument("--sans-textes", action="store_true")
    args = ap.parse_args()

    proc = ProcesseurDXF(args.fichier, inclure_textes=not args.sans_textes)
    if not proc.charger():
        raise SystemExit(1)

    data = proc.traiter()
    with open(args.sortie, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    meta = data["metadata"]
    print(f"\n{len(data['features'])} entités → {args.sortie}")
    print(f"bbox locale : {meta['bbox_locale']}")
    print(f"entités avec attributs de bloc : {meta.get('nb_entites_avec_attributs') or 0}")
    if meta.get("types_3d"):
        print(f"objets 3D volumiques (non convertis) : {meta['types_3d']}")
    print("\ncalques :")
    for calque, n in list(proc.resume_calques().items())[:40]:
        z = (meta.get("profil_z") or {}).get(calque) or {}
        flag = ""
        if z.get("probable_courbes_niveau"):
            flag = "  [courbes de niveau]"
        elif z.get("porte_altimetrie"):
            flag = f"  [Z {z.get('z_min'):.2f}→{z.get('z_max'):.2f}]"
        print(f"  {calque:<40} {n:>6}{flag}")
    blocs = meta.get("blocs") or {}
    if blocs:
        print("\nblocs :")
        for nom, info in list(blocs.items())[:30]:
            tags = ",".join(info.get("tags") or []) or "—"
            print(f"  {nom:<32} {info['nb']:>6}  {tags}")
    print("\n⚠  Fichier NON géoréférencé : aperçu en coordonnées du dessin,")
    print("   calage géographique prévu via plan_cao (étape suivante).")


if __name__ == "__main__":
    main()
