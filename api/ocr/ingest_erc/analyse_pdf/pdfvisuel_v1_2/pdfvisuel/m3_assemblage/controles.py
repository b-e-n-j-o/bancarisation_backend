"""Contrôles de cohérence déterministes entre la prose, les grilles lues et les zones vision.

Chaque contrôle renvoie des entrées {controle, niveau, page, ref, message, detail}.
Niveaux : info | attention | alerte.
"""
from __future__ import annotations

from collections import Counter

from ..m1_detection.geometrie import DocumentPDF
from ..m1_detection.temporel import NOMS_MOIS, mois_dans_prose

PAGES_EN_ARRIERE = 2   # une fiche action tient souvent sur 2-3 pages


def _noms(mois: set[int]) -> str:
    return ", ".join(NOMS_MOIS[m] for m in sorted(mois)) or "aucun"


def lignes_repetees(doc: DocumentPDF, part_min: float = 0.3) -> set[str]:
    """En-têtes et pieds de page courants : lignes identiques sur au moins 30 % des pages
    (ex. « SNC 136 Avenue d'Aquitaine - Avril 2025 », qui ferait croire à un mois cité)."""
    compte: Counter = Counter()
    for n in range(1, len(doc) + 1):
        geo = doc.page(n)
        compte.update({" ".join(m.texte for m in l) for l in geo.lignes_de_mots()})
    seuil = max(3, part_min * len(doc))
    return {l for l, n in compte.items() if n >= seuil}


def _prose_hors_grilles(doc: DocumentPDF, page_profil: dict, repetees: set[str]) -> str:
    geo = doc.page(page_profil["page"])
    boites = [g["bbox"] for g in page_profil["grilles"]]
    morceaux = []
    for ligne in geo.lignes_de_mots():
        if " ".join(m.texte for m in ligne) in repetees:
            continue
        morceaux += [m.texte for m in ligne
                     if not any(b[0] - 2 <= m.cx <= b[2] + 2 and b[1] - 2 <= m.cy <= b[3] + 2 for b in boites)]
    return " ".join(morceaux)


def mois_marques(grille: dict) -> set[int]:
    vus = set()
    for ligne in grille["lignes"]:
        for m in ligne["marques"]:
            if m["type"] in ("croix", "remplissage", "remplissage_minoritaire", "bande"):
                for i in m["colonnes"]:
                    if grille["colonnes"][i].get("mois"):
                        vus.add(grille["colonnes"][i]["mois"])
    return vus


def controle_concordance_mois(doc: DocumentPDF, profil: dict) -> list[dict]:
    """Compare les mois marqués dans chaque grille mensuelle aux mois cités dans la prose voisine.

    Le cas 'couleur_seule' est le plus important : la fenêtre d'intervention n'existe que dans
    la couleur des cellules. Une extraction OCR -> LLM la perdrait."""
    pages = {p["page"]: p for p in profil["pages"]}
    repetees = lignes_repetees(doc)
    sortie = []
    for p in profil["pages"]:
        for g in p["grilles"]:
            if g["type_axe"] != "mois":
                continue
            grille_m = mois_marques(g)
            if not grille_m:
                continue
            texte_m, portee = set(), None
            for recul in range(0, PAGES_EN_ARRIERE + 1):
                q = pages.get(p["page"] - recul)
                if q is None:
                    break
                if recul > 0 and any(x["type_axe"] == "mois" for x in q["grilles"]):
                    break  # page précédente = autre fiche avec sa propre grille de mois
                texte_m = mois_dans_prose(_prose_hors_grilles(doc, q, repetees))
                if texte_m:
                    portee = "même page" if recul == 0 else f"{recul} page(s) avant"
                    break
            types = Counter(m["type"] for l in g["lignes"] for m in l["marques"])
            par_couleur = any(t.startswith("remplissage") for t in types)
            if not texte_m:
                statut, niveau = "couleur_seule" if par_couleur else "grille_seule", "alerte"
                msg = (f"Mois marqués ({_noms(grille_m)}) absents de la prose voisine : "
                       + ("l'information n'existe que dans la COULEUR des cellules." if par_couleur
                          else "l'information n'existe que dans la grille."))
            elif texte_m == grille_m:
                statut, niveau = "concordant", "info"
                msg = f"Grille et prose concordent ({_noms(grille_m)}, prose : {portee})."
            elif texte_m & grille_m:
                statut, niveau = "partiel", "attention"
                msg = (f"Recouvrement partiel : grille {_noms(grille_m)} / prose {_noms(texte_m)} ({portee}). "
                       f"Seulement grille : {_noms(grille_m - texte_m)} ; seulement prose : {_noms(texte_m - grille_m)}.")
            else:
                statut, niveau = "divergent", "alerte"
                msg = f"Divergence : grille {_noms(grille_m)} / prose {_noms(texte_m)} ({portee})."
            sortie.append({"controle": "concordance_mois", "statut": statut, "niveau": niveau,
                           "page": p["page"], "page_imprimee": p.get("page_imprimee"), "ref": g["id"],
                           "message": msg,
                           "detail": {"mois_grille": sorted(grille_m), "mois_prose": sorted(texte_m),
                                      "portee_prose": portee}})
    return sortie


def controle_libelles_annees(profil: dict) -> list[dict]:
    """Vérifie que les libellés N+k et les années d'une même colonne sont cohérents
    (même année de référence N sur toute la grille)."""
    sortie = []
    for p in profil["pages"]:
        for g in p["grilles"]:
            if g["type_axe"] != "annees":
                continue
            cols = [c for c in g["colonnes"] if "n_debut" in c and "annee_debut" in c]
            if len(cols) < 2:
                continue
            base = Counter(c["annee_debut"] - c["n_debut"] for c in cols).most_common(1)[0][0]
            fautives = [c["libelle"] for c in cols
                        if c["annee_debut"] - c["n_debut"] != base or c["annee_fin"] - c["n_fin"] != base]
            if fautives:
                sortie.append({"controle": "libelles_annees", "statut": "incoherent", "niveau": "attention",
                               "page": p["page"], "page_imprimee": p.get("page_imprimee"), "ref": g["id"],
                               "message": f"N = {base} sur la grille, mais colonnes incohérentes : "
                                          + " ; ".join(fautives) + ". Se fier aux N+k plutôt qu'aux années écrites.",
                               "detail": {"annee_N": base, "colonnes": fautives}})
    return sortie


def controle_plages(profil: dict) -> list[dict]:
    """Marques posées sur des colonnes regroupant plusieurs années : l'année exacte d'une
    occurrence n'est pas donnée par la grille et devra venir du texte ou être confirmée."""
    sortie = []
    for p in profil["pages"]:
        for g in p["grilles"]:
            for l in g["lignes"]:
                plages = [g["colonnes"][i]["libelle"] for m in l["marques"]
                          if m["type"] in ("croix", "remplissage", "remplissage_minoritaire")
                          for i in m["colonnes"] if g["colonnes"][i].get("plage")]
                if plages:
                    sortie.append({"controle": "plage_agregee", "statut": "annee_imprecise", "niveau": "info",
                                   "page": p["page"], "page_imprimee": p.get("page_imprimee"), "ref": g["id"],
                                   "message": f"« {l['libelle'][:80]} » : {len(plages)} marque(s) sur des plages "
                                              f"pluriannuelles ({' ; '.join(plages)}) — l'année exacte n'est pas dans la grille.",
                                   "detail": {"ligne": l["libelle"], "plages": plages}})
    return sortie


def controle_zones_non_lues(profil: dict, vision: dict | None) -> list[dict]:
    lues = {z["id"] for z in (vision or {}).get("zones", []) if z.get("statut") in ("ok", "cache")}
    sortie = []
    for z in profil["routage"]:
        if z["action"] not in ("vision_zone", "ocr_page"):
            continue
        ident = z.get("ref") or f"p{z['page']:03d}_{z.get('type_zone', 'page_scannee')}"
        if ident not in lues:
            sortie.append({"controle": "zone_non_lue", "statut": "contenu_manquant",
                           "niveau": "alerte" if z["priorite"] == "haute" else "attention",
                           "page": z["page"], "ref": ident,
                           "message": f"Zone {z.get('type_zone', z['action'])} non transcrite : son contenu est absent "
                                      f"du texte. {z['motif']}",
                           "detail": {"contexte": z.get("contexte")}})
    return sortie


def tous_les_controles(doc: DocumentPDF, profil: dict, vision: dict | None) -> list[dict]:
    return (controle_concordance_mois(doc, profil) + controle_libelles_annees(profil)
            + controle_plages(profil) + controle_zones_non_lues(profil, vision))
