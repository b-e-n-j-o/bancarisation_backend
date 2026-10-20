"""Références IMAGE / PDF d'un DXF, et appariement avec un ZIP (par nom, jamais Z:\\)."""

from __future__ import annotations

import io
import logging
import math
import zipfile
from pathlib import Path
from typing import Any

from ezdxf.document import Drawing

logger = logging.getLogger(__name__)

MEDIA_EXT = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".bmp", ".webp", ".pdf",
}
TYPES_PLACES = ("IMAGE", "PDFREFERENCE", "PDFUNDERLAY", "DWFUNDERLAY", "DGNUNDERLAY")
TAILLE_FICHIER_MAX = 40 * 1024 * 1024


class DepotCao:
    """Contenu déposé : un DXF, éventuellement des médias (ZIP ou fichiers joints)."""

    def __init__(
        self,
        dxf_nom: str,
        dxf_bytes: bytes,
        fichiers: dict[str, bytes] | None = None,
        source: str = "dxf",
    ):
        self.dxf_nom = dxf_nom
        self.dxf_bytes = dxf_bytes
        self.fichiers = fichiers or {}
        self.source = source  # dxf | zip


def _norm_path(p: str) -> str:
    p = (p or "").replace("\\", "/").strip()
    if len(p) >= 2 and p[1] == ":":
        p = p[2:]
    p = p.lstrip("/").lstrip("./")
    while p.startswith("../"):
        p = p[3:]
    return p.lower()


def _nom(p: str) -> str:
    return Path(_norm_path(p)).name.lower()


def _parent(p: str) -> str:
    parts = [x for x in _norm_path(p).split("/") if x and x != ".."]
    return parts[-2] if len(parts) >= 2 else ""


def ouvrir_depot(nom: str, contenu: bytes) -> DepotCao:
    """Accepte un .dxf ou un .zip contenant un DXF + images/PDF."""
    nom_l = (nom or "plan.dxf").lower()
    if nom_l.endswith(".zip"):
        return _depuis_zip(nom, contenu)
    if nom_l.endswith(".dxf"):
        return DepotCao(nom, contenu, {}, "dxf")
    raise ValueError("Fichier DXF ou ZIP attendu.")


def medias_depuis_bytes(nom: str, contenu: bytes) -> dict[str, bytes]:
    """Médias d'un ZIP (avec ou sans DXF) ou d'un fichier image/PDF isolé."""
    nom_l = (nom or "").lower()
    if nom_l.endswith(".zip"):
        try:
            return ouvrir_depot(nom, contenu).fichiers
        except ValueError:
            return _medias_zip(contenu)
    if Path(nom_l).suffix in MEDIA_EXT:
        return {_chemin_zip_safe(nom): contenu}
    raise ValueError("ZIP, image ou PDF attendu pour relier les fichiers manquants.")


def fusionner_complements(depot: DepotCao, nom: str, contenu: bytes) -> DepotCao:
    """Ajoute les médias d'un ZIP (sans DXF) ou d'un fichier image/PDF isolé."""
    extra = medias_depuis_bytes(nom, contenu)
    fusion = dict(depot.fichiers)
    fusion.update(extra)
    return DepotCao(depot.dxf_nom, depot.dxf_bytes, fusion, depot.source)


def _chemin_zip_safe(nom: str) -> str:
    parts = [p for p in Path(nom.replace("\\", "/")).parts if p not in ("/", ".", "..")]
    return "/".join(parts) if parts else "fichier"


def _depuis_zip(nom_zip: str, contenu: bytes) -> DepotCao:
    dxfs: list[tuple[str, bytes]] = []
    medias = _medias_zip(contenu, dxfs_out=dxfs)
    if not dxfs:
        raise ValueError(
            "Le ZIP ne contient pas de DXF. Déposez le DXF d’abord, puis joignez ce ZIP "
            "pour relier les images/PDF."
        )
    dxfs.sort(key=lambda kv: (0 if "/" not in kv[0].strip("/") else 1, -len(kv[1])))
    dxf_nom, dxf_bytes = dxfs[0]
    return DepotCao(Path(dxf_nom).name or nom_zip, dxf_bytes, medias, "zip")


def _medias_zip(
    contenu: bytes,
    dxfs_out: list[tuple[str, bytes]] | None = None,
) -> dict[str, bytes]:
    medias: dict[str, bytes] = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP illisible.") from exc
    for info in zf.infolist():
        if info.is_dir():
            continue
        brut = info.filename.replace("\\", "/")
        if brut.startswith("__MACOSX/") or Path(brut).name.startswith("._"):
            continue
        if ".." in Path(brut).parts:
            continue
        ext = Path(brut).suffix.lower()
        try:
            data = zf.read(info)
        except Exception:
            continue
        if not data or len(data) > TAILLE_FICHIER_MAX:
            continue
        if ext == ".dxf" and dxfs_out is not None:
            dxfs_out.append((brut, data))
        elif ext in MEDIA_EXT:
            medias[brut] = data
    return medias


def extraire_references(doc: Drawing, facteur: float = 1.0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Références posées dans le modelspace + définitions jamais insérées."""
    msp = doc.modelspace()
    places: list[dict[str, Any]] = []
    handles_def_utilisees: set[str] = set()

    for i, e in enumerate(msp):
        t = e.dxftype()
        if t not in TYPES_PLACES:
            continue
        rec = _depuis_entite(e, doc, facteur, i)
        if not rec:
            continue
        places.append(rec)
        if rec.get("def_handle"):
            handles_def_utilisees.add(rec["def_handle"])

    non_placees: list[dict[str, Any]] = []
    for obj in doc.objects:
        t = obj.dxftype()
        if t not in ("IMAGEDEF", "PDFDEFINITION", "DWFDEFINITION", "DGNDEFINITION"):
            continue
        h = obj.dxf.handle if obj.dxf.hasattr("handle") else None
        if h and h in handles_def_utilisees:
            continue
        fn = obj.dxf.get("filename") if obj.dxf.hasattr("filename") else None
        if not fn:
            continue
        non_placees.append({
            "id": f"def-{h or _nom(fn)}",
            "dxf_type": t,
            "nom_fichier": Path(str(fn).replace("\\", "/")).name,
            "chemin_origine": str(fn),
            "statut": "non_place",
        })
    return places, non_placees


def _depuis_entite(e, doc: Drawing, facteur: float, idx: int) -> dict[str, Any] | None:
    t = e.dxftype()
    calque = str(e.dxf.layer) if e.dxf.hasattr("layer") else "0"
    handle = str(e.dxf.handle) if e.dxf.hasattr("handle") else f"{t}-{idx}"
    rec: dict[str, Any] = {
        "id": handle,
        "handle": handle,
        "dxf_type": t,
        "calque": calque,
        "statut": "manque",
        "chemin_origine": None,
        "nom_fichier": None,
        "chemin_archive": None,
        "emprise_estimee": False,
        "candidats": [],
        "apercu": None,
        "def_handle": None,
    }

    if t == "IMAGE":
        try:
            insert = e.dxf.insert
            u = e.dxf.u_pixel
            v = e.dxf.v_pixel
            sx, sy, *_ = e.dxf.image_size
            w, h = float(sx), float(sy)
        except Exception:
            return None
        fn = None
        try:
            idef = e.image_def
            if idef is not None and idef.dxf.hasattr("filename"):
                fn = idef.dxf.filename
                rec["def_handle"] = idef.dxf.handle if idef.dxf.hasattr("handle") else None
        except Exception:
            pass
        rec["chemin_origine"] = fn
        rec["nom_fichier"] = Path(str(fn).replace("\\", "/")).name if fn else f"image-{handle}"
        rec["coins"] = _coins_image(insert, u, v, w, h, facteur)
        rec["largeur"], rec["hauteur"] = _taille_coins(rec["coins"])
        return rec

    fn = None
    def_handle = None
    if e.dxf.hasattr("underlay_def_handle"):
        def_handle = str(e.dxf.underlay_def_handle)
        rec["def_handle"] = def_handle
        try:
            udef = doc.entitydb.get(def_handle)
            if udef is not None and udef.dxf.hasattr("filename"):
                fn = udef.dxf.filename
        except Exception:
            pass
    rec["chemin_origine"] = fn
    rec["nom_fichier"] = Path(str(fn).replace("\\", "/")).name if fn else f"pdf-{handle}"
    insert = e.dxf.insert if e.dxf.hasattr("insert") else (0, 0, 0)
    scale_x = float(e.dxf.scale_x) if e.dxf.hasattr("scale_x") else 1.0
    scale_y = float(e.dxf.scale_y) if e.dxf.hasattr("scale_y") else scale_x
    rotation = float(e.dxf.rotation) if e.dxf.hasattr("rotation") else 0.0
    rec["pdf_insert"] = [float(insert[0]), float(insert[1])]
    rec["pdf_scale"] = [scale_x, scale_y]
    rec["pdf_rotation"] = rotation
    rec["coins"] = _coins_pdf(insert, scale_x, scale_y, rotation, facteur, None)
    rec["emprise_estimee"] = True
    rec["largeur"], rec["hauteur"] = _taille_coins(rec["coins"])
    return rec


def _pt(x: float, y: float, facteur: float) -> list[float]:
    return [round(x * facteur, 3), round(y * facteur, 3)]


def _coins_image(insert, u, v, w: float, h: float, facteur: float) -> dict[str, list[float]]:
    ix, iy = float(insert[0]), float(insert[1])
    ux, uy = float(u[0]), float(u[1])
    vx, vy = float(v[0]), float(v[1])
    bl = _pt(ix, iy, facteur)
    br = _pt(ix + ux * w, iy + uy * w, facteur)
    tr = _pt(ix + ux * w + vx * h, iy + uy * w + vy * h, facteur)
    tl = _pt(ix + vx * h, iy + vy * h, facteur)
    return {"bas_gauche": bl, "bas_droite": br, "haut_droite": tr, "haut_gauche": tl}


def _coins_pdf(
    insert,
    scale_x: float,
    scale_y: float,
    rotation_deg: float,
    facteur: float,
    page_pouces: tuple[float, float] | None,
) -> dict[str, list[float]]:
    w_in, h_in = page_pouces if page_pouces else (11.69, 8.27)  # A3 paysage par défaut
    w = w_in * (scale_x or 1.0)
    h = h_in * (scale_y or scale_x or 1.0)
    rad = math.radians(rotation_deg or 0.0)
    c, s = math.cos(rad), math.sin(rad)
    ix, iy = float(insert[0]), float(insert[1])

    def rot(dx: float, dy: float) -> list[float]:
        return _pt(ix + dx * c - dy * s, iy + dx * s + dy * c, facteur)

    return {
        "bas_gauche": rot(0, 0),
        "bas_droite": rot(w, 0),
        "haut_droite": rot(w, h),
        "haut_gauche": rot(0, h),
    }


def _taille_coins(coins: dict[str, list[float]]) -> tuple[float, float]:
    xs = [p[0] for p in coins.values()]
    ys = [p[1] for p in coins.values()]
    return round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3)


def bbox_depuis_rasters(refs: list[dict[str, Any]]) -> dict[str, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for r in refs:
        coins = r.get("coins") or {}
        for p in coins.values():
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                xs.append(float(p[0]))
                ys.append(float(p[1]))
    if not xs:
        return None
    return {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)}


def fusionner_bbox(a: dict[str, float] | None, b: dict[str, float] | None) -> dict[str, float] | None:
    if not a:
        return b
    if not b:
        return a
    return {
        "xmin": min(a["xmin"], b["xmin"]),
        "ymin": min(a["ymin"], b["ymin"]),
        "xmax": max(a["xmax"], b["xmax"]),
        "ymax": max(a["ymax"], b["ymax"]),
    }


def apparier(
    refs: list[dict[str, Any]],
    fichiers: dict[str, bytes],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Relie chaque référence au fichier d'archive dont le nom (pas le Z:\\) correspond."""
    par_nom: dict[str, list[str]] = {}
    for chemin in fichiers:
        par_nom.setdefault(_nom(chemin), []).append(chemin)

    pris: set[str] = set()
    for ref in refs:
        origine = ref.get("chemin_origine") or ""
        nom = _nom(origine or ref.get("nom_fichier") or "")
        candidats = list(par_nom.get(nom, []))
        ref["candidats"] = candidats
        choisi = _choisir(origine, candidats, pris)
        if choisi:
            pris.add(choisi)
            ref["statut"] = "apparie"
            ref["chemin_archive"] = choisi
            ref["nom_fichier"] = Path(choisi).name
        else:
            ref["statut"] = "manque"
            ref["chemin_archive"] = None

    unused = [c for c in fichiers if c not in pris]
    return refs, unused


def _choisir(origine: str, candidats: list[str], pris: set[str]) -> str | None:
    dispo = [c for c in candidats if c not in pris]
    if not dispo:
        return None
    if len(dispo) == 1:
        return dispo[0]
    orig = _norm_path(origine)
    parent = _parent(origine)
    for c in dispo:
        nc = _norm_path(c)
        if orig and (nc.endswith(orig) or orig.endswith(nc)):
            return c
    if parent:
        meme_dossier = [c for c in dispo if _parent(c) == parent]
        if len(meme_dossier) == 1:
            return meme_dossier[0]
    return None


def appliquer_pdf_et_apercus(
    refs: list[dict[str, Any]],
    fichiers: dict[str, bytes],
    facteur: float,
) -> None:
    for ref in refs:
        chemin = ref.get("chemin_archive")
        if not chemin or chemin not in fichiers:
            continue
        data = fichiers[chemin]
        if ref["dxf_type"] != "IMAGE":
            page = _pdf_pouces(data)
            insert = ref.get("pdf_insert")
            scale = ref.get("pdf_scale") or [1, 1]
            if page and insert:
                ref["coins"] = _coins_pdf(
                    insert,
                    scale[0],
                    scale[1] if len(scale) > 1 else scale[0],
                    ref.get("pdf_rotation") or 0.0,
                    facteur,
                    page,
                )
                ref["emprise_estimee"] = False
                ref["largeur"], ref["hauteur"] = _taille_coins(ref["coins"])
            ref["apercu"] = _apercu_pdf(data) or _apercu_image(data)
        else:
            ref["apercu"] = _apercu_image(data)


def _pdf_pouces(data: bytes) -> tuple[float, float] | None:
    if data[:4] != b"%PDF":
        return None
    try:
        from pypdf import PdfReader
        page = PdfReader(io.BytesIO(data)).pages[0]
        box = page.mediabox
        return float(box.width) / 72.0, float(box.height) / 72.0
    except Exception:
        return None


def _apercu_image(data: bytes, cote: int = 512) -> str | None:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        img.thumbnail((cote, cote))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=72, optimize=True)
        import base64
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _apercu_pdf(data: bytes, cote: int = 512) -> str | None:
    if data[:4] != b"%PDF":
        return None
    try:
        import fitz  # PyMuPDF, optionnel
        doc = fitz.open(stream=data, filetype="pdf")
        page = doc[0]
        zoom = min(cote / max(page.rect.width, 1), cote / max(page.rect.height, 1), 1.5)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        import base64
        return "data:image/jpeg;base64," + base64.b64encode(pix.tobytes("jpeg")).decode("ascii")
    except Exception:
        return None


def serialiser_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sortie = []
    for r in refs:
        sortie.append({
            "id": r.get("id"),
            "handle": r.get("handle"),
            "dxf_type": r.get("dxf_type"),
            "calque": r.get("calque"),
            "nom_fichier": r.get("nom_fichier"),
            "chemin_origine": r.get("chemin_origine"),
            "chemin_archive": r.get("chemin_archive"),
            "statut": r.get("statut"),
            "emprise_estimee": bool(r.get("emprise_estimee")),
            "largeur": r.get("largeur"),
            "hauteur": r.get("hauteur"),
            "coins": r.get("coins"),
            "apercu": r.get("apercu"),
            "candidats": r.get("candidats") or [],
            "pdf_insert": r.get("pdf_insert"),
            "pdf_scale": r.get("pdf_scale"),
            "pdf_rotation": r.get("pdf_rotation"),
        })
    return sortie


def resume_rasters(refs: list[dict[str, Any]], non_placees: list[dict[str, Any]], unused: list[str]) -> dict[str, Any]:
    n_manque = sum(1 for r in refs if r.get("statut") == "manque")
    n_ok = sum(1 for r in refs if r.get("statut") == "apparie")
    return {
        "nb_references": len(refs),
        "nb_appariees": n_ok,
        "nb_manquantes": n_manque,
        "nb_definitions_non_placees": len(non_placees),
        "fichiers_archive_non_utilises": unused,
        "definitions_non_placees": non_placees,
    }
