"""Contexte conservé à l'éclatement DXF : bloc, attributs, couleurs, calques, Z.

`eclater_avec_contexte` remplace `eclater_sur` : chaque entité fille hérite du
bloc parent (nom, ATTRIB, couleur ByBlock). Sans ça, un inventaire arboré
(ARBRE / ESSENCE / DIAM_TRONC) ressort en textes flottants, détachés de leur
géométrie.

Ne construit pas de vue 3D : les volumiques (MESH, 3DSOLID…) sont comptés
puis laissés de côté. Le Z utile est celui du terrain (courbes, points cotés).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ezdxf.colors import aci2rgb
from ezdxf.document import Drawing
from ezdxf.entities import DXFEntity

logger = logging.getLogger(__name__)

TYPES_A_ECLATER = frozenset({
    "INSERT", "ACAD_PROXY_ENTITY", "ACAD_TABLE",
    "DIMENSION", "LEADER", "MULTILEADER", "MLEADER", "MLINE",
})

TYPES_ATTRIB = frozenset({"ATTRIB", "ATTDEF"})

# Types 3D volumiques : make_path ne les gère pas. On ne les convertit pas,
# mais on les COMPTE pour savoir si le fichier contient de la vraie 3D.
TYPES_3D = frozenset({
    "MESH", "POLYFACE", "SURFACE", "3DSOLID", "BODY", "REGION", "HELIX",
})

COULEUR_BYLAYER = 256
COULEUR_BYBLOCK = 0


class Contexte:
    """Ce dont hérite une entité issue d'un bloc."""

    __slots__ = ("bloc", "chemin_blocs", "attributs", "couleur_bloc")

    def __init__(self, bloc=None, chemin_blocs=None, attributs=None, couleur_bloc=None):
        self.bloc: Optional[str] = bloc
        self.chemin_blocs: List[str] = chemin_blocs or []
        self.attributs: Dict[str, str] = attributs or {}
        self.couleur_bloc: Optional[int] = couleur_bloc

    def descendre(self, insert) -> "Contexte":
        nom = _attr(insert, "name") or "?"
        attribs = dict(self.attributs)
        for a in getattr(insert, "attribs", []) or []:
            tag = str(_attr(a, "tag", "") or "").strip()
            val = _texte_attrib(a)
            if tag:
                attribs[tag] = val
        couleur = _attr(insert, "color", COULEUR_BYLAYER)
        try:
            couleur = int(couleur)
        except (TypeError, ValueError):
            couleur = COULEUR_BYLAYER
        return Contexte(
            bloc=nom,
            chemin_blocs=self.chemin_blocs + [nom],
            attributs=attribs,
            couleur_bloc=(
                couleur
                if couleur not in (COULEUR_BYLAYER, COULEUR_BYBLOCK)
                else self.couleur_bloc
            ),
        )


def _attr(e, nom: str, defaut=None):
    try:
        return e.dxf.get(nom, defaut) if e.dxf.hasattr(nom) else defaut
    except Exception:
        return defaut


def _texte_attrib(a) -> str:
    try:
        val = (a.plain_text() if hasattr(a, "plain_text") else _attr(a, "text", "")) or ""
    except Exception:
        val = str(_attr(a, "text", "") or "")
    return str(val).strip()


def eclater_avec_contexte(
    entities: Iterable[DXFEntity],
    contexte: Optional[Contexte] = None,
    compteur_3d: Optional[Dict[str, int]] = None,
    profondeur: int = 0,
) -> Iterator[Tuple[DXFEntity, Contexte]]:
    """Éclate blocs/proxies en conservant le contexte du bloc parent."""
    ctx = contexte or Contexte()
    if profondeur > 12:
        logger.warning("imbrication de blocs > 12 niveaux, arrêt de la descente")
        return

    for entity in entities:
        t = entity.dxftype()

        if compteur_3d is not None and t in TYPES_3D:
            compteur_3d[t] = compteur_3d.get(t, 0) + 1

        if t not in TYPES_A_ECLATER:
            yield entity, ctx
            continue

        sous_ctx = ctx.descendre(entity) if t == "INSERT" else ctx

        try:
            if t == "INSERT" and (getattr(entity, "mcount", 1) or 1) > 1:
                subs = list(entity.multi_insert())
            elif hasattr(entity, "virtual_entities"):
                subs = list(entity.virtual_entities())
            else:
                yield entity, ctx
                continue
        except Exception as exc:
            logger.warning("éclatement %s impossible (%s) — entité conservée", t, exc)
            yield entity, ctx
            continue

        if t == "INSERT":
            que_attribs = (not subs) or all(s.dxftype() in TYPES_ATTRIB for s in subs)
            if que_attribs and sous_ctx.attributs:
                # Pas de symbole graphique : l'INSERT reste l'ancre (point d'insertion).
                yield entity, sous_ctx
            elif not subs:
                yield entity, ctx
            if subs:
                yield from eclater_avec_contexte(subs, sous_ctx, compteur_3d, profondeur + 1)
            continue

        if not subs:
            yield entity, ctx
            continue
        yield from eclater_avec_contexte(subs, sous_ctx, compteur_3d, profondeur + 1)


# --------------------------------------------------------------- calques

def extraire_calques(doc: Drawing) -> Dict[str, Dict[str, Any]]:
    """Métadonnées de calque : couleur, visibilité AutoCAD, gel, verrou."""
    table: Dict[str, Dict[str, Any]] = {}
    for layer in doc.layers:
        nom = str(layer.dxf.name)
        try:
            eteint = bool(layer.is_off())
        except Exception:
            eteint = False
        try:
            gele = bool(layer.is_frozen())
        except Exception:
            gele = False
        couleur, aci = _couleur_calque(layer)
        table[nom] = {
            "nom": nom,
            "couleur": couleur,
            "aci": aci,
            "eteint": eteint,
            "gele": gele,
            "verrouille": _est_verrouille(layer),
            "ligne": str(layer.dxf.get("linetype", "") or ""),
            "description": str(getattr(layer, "description", "") or ""),
        }
    return table


def visible_defaut_calque(_nom: str, meta: Optional[Dict[str, Any]] = None) -> bool:
    """Éteint ou gelé AutoCAD : masqué par défaut, mais toujours extrait.

    Le calque 0 n'est plus exclu : c'est souvent là que vivent les points cotés
    (ATPOINT / MAT+ALT) et une grande partie des blocs.
    """
    if not meta:
        return True
    return not bool(meta.get("eteint") or meta.get("gele"))


def _est_verrouille(layer) -> bool:
    try:
        fn = getattr(layer, "is_locked", None)
        if callable(fn):
            return bool(fn())
        return bool(fn)
    except Exception:
        return False


def _couleur_calque(layer) -> Tuple[str, int]:
    aci = 7
    try:
        aci = abs(int(layer.dxf.get("color", 7) or 7))
    except (TypeError, ValueError):
        aci = 7
    try:
        if layer.dxf.hasattr("true_color"):
            tc = layer.dxf.true_color
            if tc is not None:
                v = int(tc)
                return (
                    f"#{(v >> 16) & 255:02x}{(v >> 8) & 255:02x}{v & 255:02x}",
                    aci,
                )
    except Exception:
        pass
    return _hex_aci(aci), aci


def _hex_aci(aci: int) -> str:
    try:
        r, g, b = aci2rgb(aci if 0 < aci < 256 else 7)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#cccccc"


def couleur_entite(entite, ctx: Contexte, calques: Dict[str, Dict[str, Any]]) -> str:
    """Résout true_color > couleur entité > couleur du bloc > ByLayer."""
    tc = _attr(entite, "true_color")
    if tc is not None:
        try:
            v = int(tc)
            return f"#{(v >> 16) & 255:02x}{(v >> 8) & 255:02x}{v & 255:02x}"
        except Exception:
            pass

    aci = _attr(entite, "color", COULEUR_BYLAYER)
    try:
        aci = int(aci)
    except (TypeError, ValueError):
        aci = COULEUR_BYLAYER

    if aci == COULEUR_BYBLOCK and ctx.couleur_bloc is not None:
        aci = ctx.couleur_bloc
    if aci not in (COULEUR_BYLAYER, COULEUR_BYBLOCK):
        return _hex_aci(aci)

    calque = str(_attr(entite, "layer", "0") or "0")
    return calques.get(calque, {}).get("couleur", "#cccccc")


# --------------------------------------------------------------- altimétrie / blocs

def profil_z_par_calque(features: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Repère les calques porteurs d'altimétrie (courbes de niveau, points cotés).

    Un calque dont les Z varient est une source topographique exploitable ;
    un calque dont tous les Z valent 0 est du dessin 2D pur.
    """
    par: Dict[str, List[float]] = {}
    for f in features:
        props = f.get("properties") or {}
        geom = f.get("geometry") or {}
        calque = props.get("calque") or "0"
        coords = geom.get("coordinates")
        if coords is None:
            continue
        zs = par.setdefault(calque, [])
        for c in _coords(coords):
            if len(c) > 2:
                zs.append(float(c[2]))

    sortie: Dict[str, Dict[str, Any]] = {}
    for calque, zs in par.items():
        if not zs:
            continue
        zmin, zmax = min(zs), max(zs)
        distincts = len({round(z, 3) for z in zs})
        sortie[calque] = {
            "z_min": zmin,
            "z_max": zmax,
            "amplitude": zmax - zmin,
            "niveaux_distincts": distincts,
            "nb_sommets_z": len(zs),
            "porte_altimetrie": (zmax - zmin) > 0.01,
            "probable_courbes_niveau": distincts > 5 and (zmax - zmin) > 1.0,
        }
    return sortie


def inventaire_blocs(features: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Compte les entités rattachées à un nom de bloc, avec les tags d'attributs."""
    par: Dict[str, Dict[str, Any]] = {}
    for f in features:
        props = f.get("properties") or {}
        bloc = props.get("bloc")
        if not bloc:
            continue
        rec = par.setdefault(bloc, {"nb": 0, "_tags": set(), "echantillon": []})
        rec["nb"] += 1
        attrs = props.get("attributs") or {}
        if isinstance(attrs, dict) and attrs:
            rec["_tags"].update(str(k) for k in attrs.keys())
            if len(rec["echantillon"]) < 8:
                rec["echantillon"].append({str(k): str(v) for k, v in attrs.items()})
    sortie: Dict[str, Dict[str, Any]] = {}
    for nom, rec in sorted(par.items(), key=lambda kv: -kv[1]["nb"]):
        sortie[nom] = {
            "nb": rec["nb"],
            "tags": sorted(rec["_tags"]),
            "echantillon": rec["echantillon"],
        }
    return sortie


def _coords(c):
    if c and isinstance(c[0], (int, float)):
        yield c
    else:
        for sub in c or []:
            yield from _coords(sub)
