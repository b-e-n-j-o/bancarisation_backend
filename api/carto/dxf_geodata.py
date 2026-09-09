"""CRS déclaré dans un DXF Civil 3D (GEODATA) et confrontation à la détection.

Le GEODATA, s'il existe, fait autorité pour le *dessin* — ce n'est pas une
preuve que toutes les géométries sont dans ce système (un mixte CC / Lambert-93
reste possible). La détection (`dxf_crs_clusters.analyser`) reste donc
obligatoire ; on compare ensuite déclaration formulaire, GEODATA et clusters.

Point d'entrée pour l'ingestion : `analyser_crs(features, doc, code_formulaire)`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from api.carto.dxf_crs_clusters import analyser, nom_crs

logger = logging.getLogger(__name__)

CODES_FORMULAIRE = {"local", "inconnu"}
SRID_CC = {3900 + z: f"RGF93 / CC{z}" for z in range(42, 51)}
SRID_CONNUS: Dict[int, str] = {
    2154: "RGF93 / Lambert-93",
    **SRID_CC,
    27572: "NTF / Lambert II étendu (ancien)",
    32630: "WGS84 / UTM 30N",
    32631: "WGS84 / UTM 31N",
    4326: "WGS84 degrés",
}

_EPSG_RE = re.compile(r"(?:EPSG[:\s_-]*|AUTHORITY\s*\[\s*\"EPSG\"\s*,\s*\")(\d{4,5})", re.I)
_CC_RE = re.compile(r"\bCC\s*[-_]?(\d{2})\b", re.I)
_L93_RE = re.compile(r"LAMBERT[\s_-]*93|\bL93\b|RGF93[.\s]*LAMBERT", re.I)


def srid_depuis_code(code: Optional[str]) -> Optional[int]:
    """'2154' / '3945' → int ; 'local' / 'inconnu' / vide → None."""
    if not code:
        return None
    c = str(code).strip().lower()
    if c in CODES_FORMULAIRE or c in ("", "0", "none", "null"):
        return None
    try:
        srid = int(c)
    except (TypeError, ValueError):
        return None
    return srid if srid > 0 else None


def libelle_srid(srid: Optional[int]) -> str:
    if srid is None:
        return "non déclaré"
    return SRID_CONNUS.get(srid) or nom_crs(srid) or f"EPSG:{srid}"


def srid_depuis_definition(texte: str) -> tuple[Optional[int], Optional[str]]:
    if not texte:
        return None, None
    m = _EPSG_RE.search(texte)
    if m:
        srid = int(m.group(1))
        return srid, libelle_srid(srid)
    if _L93_RE.search(texte):
        return 2154, SRID_CONNUS[2154]
    m = _CC_RE.search(texte)
    if m:
        zone = int(m.group(1))
        if 42 <= zone <= 50:
            srid = 3900 + zone
            return srid, SRID_CONNUS[srid]
    if re.search(r"LAMBERT\s*II|LAMB2|NTF", texte, re.I):
        return 27572, SRID_CONNUS[27572]
    return None, None


def lire_geodata(doc) -> Optional[Dict[str, Any]]:
    """Lit le GEODATA Civil 3D / AutoCAD (WKT ou XML du système de coordonnées)."""
    if doc is None:
        return None
    gd = None
    try:
        gd = doc.modelspace().get_geodata()
    except Exception:
        gd = None
    if gd is None:
        try:
            for obj in doc.objects:
                if obj.dxftype() == "GEODATA":
                    gd = obj
                    break
        except Exception:
            gd = None
    if gd is None:
        return None

    morceaux: list[str] = []
    for attr in (
        "coordinate_system_definition",
        "coordinate_system_code",
        "coordinate_system_datum",
    ):
        try:
            if gd.dxf.hasattr(attr):
                val = gd.dxf.get(attr)
                if val:
                    morceaux.append(str(val))
        except Exception:
            continue
    for meth in ("get_crs", "coordinate_system_definition"):
        try:
            val = getattr(gd, meth, None)
            if callable(val):
                val = val()
            if val:
                morceaux.append(str(val))
        except Exception:
            continue

    definition = "\n".join(morceaux).strip()
    srid, nom = srid_depuis_definition(definition)
    if not srid and not definition:
        return None
    logger.info("GEODATA : srid=%s (%s)", srid, nom)
    return {
        "srid": srid,
        "nom": nom,
        "definition": definition[:4000] if definition else None,
        "origine": "geodata",
    }


def comparer_declaration(
    code_formulaire: Optional[str],
    srid_formulaire: Optional[int],
    geodata: Optional[Dict[str, Any]],
    analyse: Dict[str, Any],
) -> Dict[str, Any]:
    rec = analyse.get("recoupement") or {}
    conclusion = rec.get("conclusion")
    detectes: list[int] = []
    if rec.get("meilleur"):
        detectes = sorted({
            int(a["srid"]) for a in rec["meilleur"].get("attribution") or []
            if a.get("srid")
        })

    srid_geodata = (geodata or {}).get("srid") if geodata else None
    if srid_geodata:
        origine = "geodata"
        srid_autorite = int(srid_geodata)
    elif srid_formulaire:
        origine = "formulaire"
        srid_autorite = int(srid_formulaire)
    else:
        origine = "formulaire"
        srid_autorite = None

    code = (code_formulaire or "inconnu").strip().lower()
    accord: Optional[bool] = None
    message = rec.get("message") or ""

    if conclusion in ("aucune_combinaison_plausible", "aucun_candidat"):
        if code == "local":
            accord = True
            message = (
                "Coordonnées locales / chantier, cohérent avec votre déclaration. "
                "Calage manuel à deux points."
            )
        elif srid_formulaire:
            accord = False
            message = (
                f"Vous avez déclaré {libelle_srid(srid_formulaire)} (EPSG:{srid_formulaire}), "
                "mais aucune combinaison ne place le dessin en France. "
                "Coordonnées locales probables — calage manuel."
            )
        else:
            accord = None
            message = (
                "Aucun système projeté reconnu. Coordonnées de chantier probables : "
                "calage manuel à deux points."
            )
    elif conclusion == "crs_unique":
        srid_d = detectes[0] if detectes else None
        if srid_formulaire and srid_d and srid_formulaire != srid_d:
            accord = False
            message = (
                f"Vous avez déclaré {libelle_srid(srid_formulaire)} (EPSG:{srid_formulaire}) ; "
                f"le cluster majoritaire indique {libelle_srid(srid_d)} (EPSG:{srid_d}). "
                "À faire trancher."
            )
        elif srid_formulaire and srid_d:
            accord = True
            message = (
                f"Détecté : {libelle_srid(srid_d)} (EPSG:{srid_d}), "
                "cohérent avec votre déclaration."
            )
        elif srid_d:
            accord = None
            message = f"Détecté : {libelle_srid(srid_d)} (EPSG:{srid_d})."
        if srid_geodata and srid_d and int(srid_geodata) != srid_d:
            accord = False
            message = (
                f"GEODATA Civil 3D : {libelle_srid(int(srid_geodata))} "
                f"(EPSG:{srid_geodata}) ; détection : {libelle_srid(srid_d)} "
                f"(EPSG:{srid_d})."
            )
    elif conclusion == "multi_crs_confirme":
        noms = ", ".join(f"{libelle_srid(s)} (EPSG:{s})" for s in detectes)
        message = (
            f"Fichier multi-CRS confirmé : {noms}. "
            "Ni AutoCAD ni QGIS ne le signalent au dessinateur — c'est un défaut réel du fichier."
        )
        if srid_formulaire and srid_formulaire not in detectes:
            accord = False
            message += (
                f" Votre déclaration {libelle_srid(srid_formulaire)} "
                f"(EPSG:{srid_formulaire}) ne correspond à aucun des nuages."
            )
        elif srid_formulaire:
            accord = None
            message += (
                f" Votre déclaration {libelle_srid(srid_formulaire)} "
                "couvre une partie du fichier ; attribuez le reste calque par calque."
            )
    elif conclusion == "incertain":
        message = rec.get("message") or (
            "Recoupement géographique incertain : à confirmer manuellement."
        )

    return {
        "accord": accord,
        "message": message,
        "srid_formulaire": srid_formulaire,
        "code_formulaire": code,
        "srid_geodata": int(srid_geodata) if srid_geodata else None,
        "srid_detectes": detectes,
        "srid_autorite": srid_autorite,
        "origine_autorite": origine,
        "conclusion": conclusion,
    }


def zone_cc_depuis_latitude(lat: Optional[float]) -> Optional[int]:
    """Le n° de zone CC est la latitude du parallèle d'origine (CC45 ≈ 45°N)."""
    if lat is None:
        return None
    zone = int(round(lat))
    if 42 <= zone <= 50:
        return zone
    return None


def enrichir_calques_crs(
    calques: list[dict[str, Any]],
    analyse_crs: Dict[str, Any],
) -> list[dict[str, Any]]:
    """Copie les propositions srid_source sur les calques (prêtes pour l'INSERT)."""
    par = {
        r["calque"]: r
        for r in (analyse_crs.get("srid_par_calque") or [])
        if isinstance(r, dict) and r.get("calque")
    }
    for c in calques:
        info = par.get(c.get("nom")) or {}
        srid = info.get("srid_source")
        ambigu = bool(info.get("ambigu"))
        c["srid_source"] = srid
        c["srid_origine"] = "detection" if srid else "herite"
        c["srid_confiance"] = info.get("confiance") or ("inconnue" if not srid else None)
        c["cluster_id"] = info.get("cluster_dominant")
        c["srid_ambigu"] = ambigu
        c["crs_nom"] = info.get("crs_nom")
    return calques


def analyser_crs(
    features: list,
    doc=None,
    code_formulaire: Optional[str] = "inconnu",
) -> Dict[str, Any]:
    """Pipeline : GEODATA → clusters → comparaison avec le formulaire.

    À appeler entre `ProcesseurDXF.traiter()` et l'écriture PostGIS.
    """
    geodata = None
    try:
        geodata = lire_geodata(doc)
    except Exception as exc:
        logger.warning("GEODATA illisible : %s", exc)

    analyse = analyser(features)
    srid_form = srid_depuis_code(code_formulaire)
    comparaison = comparer_declaration(code_formulaire, srid_form, geodata, analyse)
    analyse["geodata"] = geodata
    analyse["comparaison"] = comparaison
    analyse["declaration"] = {
        "code": (code_formulaire or "inconnu").strip().lower(),
        "srid": srid_form,
        "origine": "formulaire",
    }
    return analyse
