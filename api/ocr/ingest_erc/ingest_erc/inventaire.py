"""Étape 0 — inventaire du dépôt.

Hash, rôle, texte des PDF, pages scannées, annexes embarquées et doublons.
100 % déterministe.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from pathlib import Path

from . import config
from .mdnorm import retirer_entetes
from .modeles import Document
from .ocr import SEUIL_PAGE_IMAGE, texte_pdf


def norm(s: str) -> str:
    s = re.sub(r"[’'`]", " ", s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).lower().strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# indices de rôle. « plan de gestion » l'emporte sur une citation d'arrêté en tête
# (les plans embarquent souvent l'arrêté en annexe / en page de garde).
INDICES_ROLE = [
    ("plan_gestion", r"plan de gestion"),
    ("arrete", r"\barrete\b.{0,80}\b(portant|prescri|autoris|derogation)"),
    ("execution", r"(decompte|compte[- ]rendu de chantier|bilan de suivi|rapport de suivi)"),
]


def role_pdf(pages, nom: str = "") -> tuple[str, str]:
    nnom = norm(nom).replace("_", " ").replace("-", " ")
    if "plan de gestion" in nnom:
        return "plan_gestion", "nom de fichier"
    tete = norm(" ".join(p.texte for p in pages[:6]))[:8000]
    # le motif qui apparaît LE PLUS TÔT gagne (un sommaire de plan cite aussi des arrêtés)
    trouves = [(m.start(), role, m.group(0)) for role, motif in INDICES_ROLE
               for m in [re.search(motif, tete)] if m]
    if trouves:
        _, role, txt = min(trouves)
        return role, f"motif « {txt[:60]} » en tête de document"
    return "autre", "aucun motif de rôle reconnu"


RE_ANNEXE_TDM = re.compile(
    r"annexe\s*n\s*°?\s*(\d+)\s*[-–:]?\s*([a-z][^\n]+?)\s*(?:\.{3,}\s*)?(\d{1,3})?\s*$",
    re.M,
)
RE_ANNEXE_TITRE = re.compile(
    r"^\s*(?:#\s*)?(?:[\divx]+(?:\.\d+)*\.?\s+)?annexe\s*n\s*°?\s*(\d+)\s*[-–:]\s*(.+)$",
    re.I,
)


def annexes_embarquees(doc: Document) -> list[dict]:
    """Annexes listées en table des matières + plage de pages estimée."""
    par_num: dict[int, dict] = {}
    tdm = "\n".join(norm(l) for p in doc.pages[:6] for l in p.texte.splitlines())
    for m in RE_ANNEXE_TDM.finditer(tdm):
        num, titre = int(m.group(1)), m.group(2).strip(" .")
        par_num.setdefault(num, {"num": num, "titre": titre})

    for p in doc.pages[6:]:
        for l in p.texte.splitlines():
            m = RE_ANNEXE_TITRE.match(l.strip())
            if not m:
                continue
            num = int(m.group(1))
            titre = re.sub(r"\s+\d{1,3}\s*$", "", m.group(2)).strip(" .")
            a = par_num.setdefault(num, {"num": num, "titre": titre})
            a.setdefault("page_debut", p.num)
            if len(titre) > len(a.get("titre") or ""):
                a["titre"] = titre
            break

    annexes = list(par_num.values())
    for a in annexes:
        if "page_debut" in a:
            continue
        motif = re.compile(rf"^\s*(?:#\s*)?(?:[\divx]+(?:\.\d+)*\.?\s+)?annexe\s*n\s*°?\s*{a['num']}(?!\d)")
        for p in doc.pages[6:]:
            if any(motif.match(norm(l)) for l in p.texte.splitlines()):
                a["page_debut"] = p.num
                break
    annexes.sort(key=lambda a: (a.get("page_debut", 10**6), a["num"]))
    for i, a in enumerate(annexes):
        nxt = annexes[i + 1].get("page_debut") if i + 1 < len(annexes) else None
        if "page_debut" in a:
            a["page_fin"] = (nxt - 1) if nxt else len(doc.pages)
        titre_n = norm(a.get("titre") or "")
        if "arrete" in titre_n:
            a["role_probable"] = "arrete"
        elif re.search(r"cout|budget|estimati|financ", titre_n):
            a["role_probable"] = "budget"
    return annexes


def _shingles(txt: str, k: int = 5) -> set:
    w = norm(txt).split()
    return {" ".join(w[i:i + k]) for i in range(max(0, len(w) - k + 1))}


def detecter_doublons(docs: list[Document]) -> None:
    """Rattache les annexes 'arrêté' d'un plan de gestion aux arrêtés déposés."""
    arretes = [d for d in docs if d.role == "arrete"]
    for d in docs:
        for a in d.sous_documents:
            if a.get("role_probable") != "arrete" or "page_debut" not in a:
                continue
            plage = [p for p in d.pages if a["page_debut"] <= p.num <= a["page_fin"]]
            scannees = sum(1 for p in plage if p.nb_mots < SEUIL_PAGE_IMAGE)
            meilleur = None
            for ar in arretes:
                titre_ar = norm(" ".join(p.texte for p in ar.pages[:1]))
                # 1) texte disponible : similarité de contenu
                sh_a = set().union(*(_shingles(p.texte) for p in plage)) if plage else set()
                sh_b = set().union(*(_shingles(p.texte) for p in ar.pages))
                jacc = len(sh_a & sh_b) / max(1, len(sh_a | sh_b))
                # 2) sinon : recouvrement du titre de l'annexe avec la tête de l'arrêté
                mots = [m for m in re.findall(r"[a-z]{5,}", a["titre"])]
                recouv = sum(m in titre_ar for m in mots) / max(1, len(mots))
                score = max(jacc, recouv * 0.9)
                if not meilleur or score > meilleur[1]:
                    meilleur = (ar.nom, score, jacc, recouv)
            if meilleur and meilleur[1] >= 0.6:
                a["doublon_de"] = meilleur[0]
                a["score_doublon"] = round(meilleur[1], 2)
                a["methode_doublon"] = "contenu" if meilleur[2] >= 0.6 else "titre (pages scannées)"
            if plage and scannees >= 0.7 * len(plage):
                a["pages_scannees"] = True
                a["texte_ocr"] = sum(len(p.texte) for p in plage) > 500
                if "doublon_de" not in a and not a["texte_ocr"]:
                    d.avertissements.append(
                        f"Annexe {a['num']} ({a['titre'][:60]}) : pages scannées, "
                        "arrêté non fourni séparément → OCR Mistral requis pour l'exploiter")


def inventorier(chemins: list[str]) -> list[Document]:
    config.charger_dotenv()
    docs = []
    for c in chemins:
        p = Path(c)
        if p.name.startswith(".") or p.name.startswith("._"):
            continue
        ext = p.suffix.lower()
        sha = sha256(p)
        if ext == ".pdf":
            pages = texte_pdf(str(p), sha)
            nettoyes = retirer_entetes([pg.texte for pg in pages])
            repetees = list(retirer_entetes.dernier_bruit)
            for pg, t in zip(pages, nettoyes):
                pg.texte = t
            role, indice = role_pdf(pages, p.name)
            d = Document(nom=p.name, chemin=str(p), sha256=sha, extension=ext,
                         role=role, role_indice=indice, pages=pages,
                         pages_image=[pg.num for pg in pages if pg.nb_mots < SEUIL_PAGE_IMAGE],
                         lignes_repetees=repetees)
            if role == "plan_gestion":
                d.sous_documents = annexes_embarquees(d)
        elif ext in (".xlsx", ".xlsm"):
            d = Document(nom=p.name, chemin=str(p), sha256=sha, extension=ext,
                         role="tableur", role_indice="extension tableur")
        elif ext == ".zip":
            noms = zipfile.ZipFile(p).namelist()
            geo = [n for n in noms if n.lower().endswith((".shp", ".gpkg", ".geojson"))
                   and "__macosx" not in n.lower() and "/._" not in n]
            role = "sig" if geo else "autre"
            d = Document(nom=p.name, chemin=str(p), sha256=sha, extension=ext, role=role,
                         role_indice=f"{len(geo)} couche(s) géographique(s) dans l'archive")
        elif ext in (".gpkg", ".geojson"):
            d = Document(nom=p.name, chemin=str(p), sha256=sha, extension=ext,
                         role="sig", role_indice="extension SIG")
        else:
            d = Document(nom=p.name, chemin=str(p), sha256=sha, extension=ext,
                         role="autre", role_indice="type non géré en v0")
        docs.append(d)
    detecter_doublons(docs)
    # doublons stricts de fichier
    vus = {}
    for d in docs:
        if d.sha256 in vus:
            d.avertissements.append(f"Fichier identique à {vus[d.sha256]}")
        vus.setdefault(d.sha256, d.nom)
    return docs
