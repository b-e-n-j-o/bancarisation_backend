"""Étape 1b — faits de référentiel.

Chaque source produit des `Fait` indépendants ; aucune fusion ici.
  - tableur  : tableaux de synthèse, légendes UG, blocs (codes, parcelles, ancres), totaux
  - plan     : en-têtes de fiches (code, UG, parcelles), familles de codes, ancres
               (+ LLM sur les sections retenues si une clé Mistral est présente)
  - arrêté   : identifiants de décision, horizon, obligations surfaciques
"""
from __future__ import annotations

import re
from collections import defaultdict

from openpyxl.utils import get_column_letter

from . import llm
from .cadastre import parse_ref
from .excel_profil import code_norm, ugs_dans
from .inventaire import norm
from .modeles import Fait, Source
from .pdf_sections import selectionner


def _f(type_, cle, valeur, doc, loc, extrait=None, methode="deterministe", conf=1.0):
    return Fait(type=type_, cle=cle, valeur=valeur, methode=methode, confiance_extraction=conf,
                source=Source(doc=doc, loc=loc, extrait=(extrait or "")[:160] or None))


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


# ================================================================== tableur
def faits_tableur(nom: str, prof: dict) -> list[Fait]:
    out = []
    for s in prof["syntheses"]:
        loc = f"{s.feuille}!{s.cellule_ug}"
        out.append(_f("action_ug", s.code, s.ugs, nom, loc,
                      f"{s.code} → {', '.join(s.ugs)}",
                      conf=0.7 if "fusion" in s.cellule_ug else 1.0))
        for k, v in s.champs.items():
            if "objectif" in k:
                if "long terme" in k:
                    out.append(_f("action_objectif", s.code, v, nom, f"{s.feuille}!L{s.ligne}", v))
            elif "operation" in k or "intitule" in k or "libelle" in k or "action" in k:
                out.append(_f("action_intitule", s.code, v, nom, f"{s.feuille}!L{s.ligne}", v))
            elif "espece" in k or "cible" in k:
                if v.strip("- "):
                    out.append(_f("action_cible", s.code, v, nom, f"{s.feuille}!L{s.ligne}", v))
    for ug, (lib, loc) in prof["ug_libelles"].items():
        out.append(_f("ug_libelle", ug, lib, nom, loc, f"{ug} : {lib}"))

    vus_parcelles = set()
    for f, b in prof["blocs"]:
        if b.suite_de is None:
            out.append(_f("ancre_temporelle", "projet",
                          {"etat_zero": b.ancre_etat_zero, "N": b.ancre_N,
                           "bloc": b.id, "titre": b.titre, "nature": b.nature},
                          nom, f"{b.feuille}!L{b.ligne_entete}-{b.ligne_rang}",
                          f"{b.titre[:50]} : état zéro {b.ancre_etat_zero}, N = {b.ancre_N}"))
            out.append(_f("bloc_codes", b.id, {"titre": b.titre, "codes": sorted({c for c in b.codes.values() if c}),
                                               "parcelles": sorted(set(b.parcelles.values())),
                                               "nature": b.nature},
                          nom, f"{b.feuille}!L{b.lignes[0] if b.lignes else b.ligne_entete}",
                          b.titre))
        for lr in b.lignes:
            code = b.codes.get(lr)
            par = b.parcelles.get(lr)
            c_surf = b.cols_id.get("surface")
            surf = f.get(lr, c_surf).v if c_surf else None
            cle = (code, par, surf)
            if code and par and cle not in vus_parcelles:
                vus_parcelles.add(cle)
                out.append(_f("code_parcelle", code, {"parcelle": par, "surface_ha": surf,
                                                      "bloc": b.suite_de or b.id},
                              nom, f"{b.feuille}!{get_column_letter(b.cols_id['numero'])}{lr}",
                              f"{code} {par} {surf} ha"))
    for t in prof["totaux"]:
        if t["k"] and t["k"] != 1.0 and t["scores"][t["k"]] >= 5:
            out.append(_f("coef_totaux", "projet", t, nom, t["feuille"],
                          f"totaux = montants × {t['k']} ({t['scores'][t['k']]}/{t['nb_totaux']} totaux expliqués)"))
    abrev = defaultdict(list)
    for k, v in prof["legendes"].items():
        abrev[norm(v)].append(k)
    for lib, codes in abrev.items():
        if len(codes) > 1:
            out.append(_f("legende_conflit", "projet", {"libelle": lib, "codes": codes}, nom,
                          "légendes", f"« {lib} » abrégé {' et '.join(codes)}"))
    return out


# ================================================================== plan de gestion
RE_FAMILLE = re.compile(r"([A-Za-zÀ-ÿœŒ’' ]{6,60}?)\s*\(([A-Z]{2,4})\)\s*:")


def _champ_fiche(lignes: list[str], libelle: str) -> tuple[str, int] | None:
    for i, l in enumerate(lignes):
        m = re.search(rf"(?i)(?:{libelle})\s*(?:\(s\))?\s*:\s*(.*)$", l)
        if m:
            val = m.group(1).strip()
            j = i + 1
            # continuation : lignes suivantes non vides sans autre « Libellé : »
            while j < len(lignes) and lignes[j].strip() and not re.search(r"^\s*[A-ZÉ][\w’' ]{3,40}\s*:", lignes[j]):
                val += " " + lignes[j].strip()
                j += 1
            return val, i
    return None


def faits_plan(doc, carte=None, regles=None) -> tuple[list[Fait], dict]:
    from .carte import Regles
    from .pdf_sections import selectionner_par_carte
    regles = regles or Regles.defaut()
    out = []
    sel = selectionner_par_carte(doc.pages, carte, regles) if carte else selectionner(doc.pages)
    nom = doc.nom
    if carte:
        for fam in carte.vocabulaire.action_familles:
            c = fam.citation
            out.append(_f("famille_code", fam.prefixe.upper(), fam.libelle, nom,
                          f"p.{c.page}" if c else "carte", c.texte if c else "carte du document",
                          methode="llm", conf=0.9 if c else 0.7))
    for s in sel["retenues"]:
        lignes = s.extrait.splitlines()
        if s.fiche:
            m = re.match(r"([A-Za-z]{1,6}\s?[-.]?\s?\d{1,3}[a-z]?)\s*[:–-]\s*(.+)", s.titre)
            code = getattr(s, "code", None) or (regles.code(m.group(1)) if m else None)
            titre = m.group(2) if m else s.titre
            if not code:
                continue
            if not carte and len(lignes) > 1 and lignes[1].strip() and ":" not in lignes[1]:
                titre += " " + lignes[1].strip()
            out.append(_f("action_intitule", code, titre.strip(), nom, f"p.{s.page_debut}", titre))
            ch = regles.champs
            ug = _champ_fiche(lignes, ch["unites"]) if ch.get("unites") else None
            if ug and regles.unites(ug[0]):
                out.append(_f("action_ug", code, regles.unites(ug[0]), nom, f"p.{s.page_debut}",
                              f"{ug[0]}"))
            par = _champ_fiche(lignes, ch["parcelles"]) if ch.get("parcelles") else None
            if par:
                refs = []
                for tok in re.split(r",\s*|;\s*|\bet\b", par[0]):
                    partie = "en partie" in tok
                    brut = re.sub(r"\(.*?\)", "", tok).strip(" ,.")
                    p = parse_ref(brut) if brut and re.search(r"\d{3,}", brut) else None
                    if p:
                        refs.append({**p, "en_partie": partie})
                if refs:
                    out.append(_f("action_parcelles_pdf", code, refs, nom, f"p.{s.page_debut}",
                                  f"Parcelles : {par[0]}"))
            obj = _champ_fiche(lignes, ch["objectif"]) if ch.get("objectif") else None
            if obj:
                out.append(_f("action_objectif", code, obj[0], nom, f"p.{s.page_debut}", obj[0]))
        for m in RE_FAMILLE.finditer(s.texte):
            lib = m.group(1).strip()
            lib = re.sub(r"^(les|la|le|des)\s+", "", lib, flags=re.I)
            if m.group(2) in sel["familles"]:
                out.append(_f("famille_code", m.group(2), lib, nom, f"p.{s.page_debut}", m.group(0)))

    if carte and carte.conventions.citations and carte.conventions.etat_zero:
        cv = carte.conventions
        c0 = cv.citations[0]
        out.append(_f("ancre_temporelle", "projet",
                      {"etat_zero": cv.etat_zero, "N": cv.annee_N or cv.etat_zero + 1,
                       "bloc": "carte", "titre": "carte du document", "nature": "llm"},
                      nom, f"p.{c0.page}", c0.texte, methode="llm", conf=0.8))
        if cv.annee_fin:
            out.append(_f("horizon", "projet", {"fin": cv.annee_fin}, nom, f"p.{c0.page}", c0.texte,
                          methode="llm", conf=0.8))

    # conventions temporelles (tout le texte : c'est du regex, pas du LLM)
    for p in doc.pages:
        lignes = p.texte.splitlines()
        for i, l in enumerate(lignes):
            nl = norm(l)
            if "etat zero" in nl and re.search(r"annee n\b", nl):
                for l2 in lignes[i + 1:i + 4]:
                    ans = [int(x) for x in re.findall(r"\b(19\d\d|20\d\d)\b", l2)]
                    if len(ans) >= 3 and ans[1] == ans[0] + 1:
                        out.append(_f("ancre_temporelle", "projet",
                                      {"etat_zero": ans[0], "N": ans[1], "bloc": f"p.{p.num}",
                                       "titre": "tableau de coûts", "nature": "couts"},
                                      nom, f"p.{p.num}", f"État zéro {ans[0]} / Année N {ans[1]}"))
                        break
            m = re.search(r"p[ée]riode (\d{4}) [àa] (\d{4})", nl)
            if m:
                out.append(_f("horizon", "projet", {"debut": int(m.group(1)), "fin": int(m.group(2))},
                              nom, f"p.{p.num}", l.strip()))
    # dédoublonner les ancres identiques par page
    vues, uniq = set(), []
    for f_ in out:
        k = (f_.type, f_.cle, str(f_.valeur) if f_.type != "ancre_temporelle" else
             (f_.valeur["etat_zero"], f_.valeur["N"]))
        if f_.type in ("ancre_temporelle", "horizon") and k in vues:
            continue
        vues.add(k)
        uniq.append(f_)
    out = uniq

    if not carte and llm.actif():  # pragma: no cover
        try:
            ref = llm.extraire_referentiel(
                [{"section": s.titre, "pages": f"{s.page_debut}-{s.page_fin}", "texte": s.extrait}
                 for s in sel["retenues"]], {"familles_detectees": sel["familles"]})
        except Exception as err:  # noqa: BLE001
            print(f"   ⚠️  LLM référentiel ignoré : {err}", flush=True)
            ref = None
        if ref:
            for a in ref.actions:
                c = code_norm(a.code) or a.code
                out.append(_f("action_ug", c, a.ugs, nom, f"p.{a.page}", methode="llm", conf=0.8))
                out.append(_f("action_intitule", c, a.intitule, nom, f"p.{a.page}", methode="llm", conf=0.8))
            for u in ref.ugs:
                if u.libelle:
                    out.append(_f("ug_libelle", u.code, u.libelle, nom, f"p.{u.page}", methode="llm", conf=0.8))
    return out, sel


# ================================================================== arrêté
MOIS = {m: i for i, m in enumerate(["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
                                     "aout", "septembre", "octobre", "novembre", "decembre"], 1)}


def faits_arrete(doc, pages=None, nom=None, cle="projet") -> list[Fait]:
    """Extrait les identifiants et obligations d'une décision. `cle` permet de traiter une
    décision secondaire (annexe) sans écraser les champs du projet."""
    out, nom = [], nom or doc.nom
    pages = pages or doc.pages
    _f0 = globals()["_f"]

    def _f(type_, c, *a, **k):  # noqa: F811 - redirige la clé 'projet'
        return _f0(type_, cle if c == "projet" else c, *a, **k)

    full = "\n".join(p.texte for p in pages)
    nfull = norm(full)

    def page_de(motif):
        for p in pages:
            if re.search(motif, norm(p.texte)):
                return f"p.{p.num}"
        return "?"

    tete = " ".join(" ".join(p.texte for p in pages[:2]).split())
    p1 = f"p.{pages[0].num}" if pages else "?"
    m = re.search(r"(ARR[ÊE]T[ÉE]\s+portant.+?)(?:R[ée]f\.|La Pr[ée]f)", tete)
    if m:
        out.append(_f("decision_objet", "projet", m.group(1).strip(), nom, p1, m.group(1)))
    m = re.search(r"habitats\s+([A-ZÉ][^()]{10,140}?\(\d{2}\))", tete)
    if not m:
        m = re.search(
            r"((?:Construction|Operation|Projet)\s+d['’]?un[^.]{8,140}?\(\d{2}\))",
            tete, re.I,
        )
    if m:
        out.append(_f("projet_libelle", "projet", m.group(1).strip(), nom, p1, m.group(1)))
    m = re.search(r"R[ée]f\.?\s*([A-Z]{2,6})\s*:?\s*n\s*°\s*([\w/.-]+)", full)
    m2 = re.search(r"Arr[êe]t[ée]\s+n\s*°\s*([\w/.-]+)", full)
    if m:
        out.append(_f("decision_reference", "projet", f"{m.group(1)} {m.group(2)}", nom, p1, m.group(0)))
    elif m2:
        out.append(_f("decision_reference", "projet", f"n° {m2.group(1)}", nom, p1, m2.group(0)))
    dates = re.findall(r"(?:Fait [àa] )?[A-Z][a-zé]+, le (\d{1,2}(?:er)?) ([A-Za-zÉéû]+)\.? (\d{4})", full)
    if dates:
        j, mo, a = dates[-1]
        mo_n = next((v for k, v in MOIS.items() if k[:3] == norm(mo)[:3]), None)
        if mo_n:
            out.append(_f("decision_date", "projet", f"{a}-{mo_n:02d}-{int(j.rstrip('er')):02d}",
                          nom, page_de(rf"le {j} {norm(mo)[:3]}"), f"le {j} {mo} {a}"))
    m = re.search(r"b[ée]n[ée]ficiaire de la d[ée]rogation est (.+?)(?:\s[–-]\s|,|\.)", full)
    if m:
        out.append(_f("maitre_ouvrage", "projet", m.group(1).strip(), nom, page_de("beneficiaire de la derogation"), m.group(0)))
    m = re.search(r"sur la commune (?:du|de la|de|d')\s*([A-ZÉ][\w\s'-]+?)\s*\((\d{2})\)", tete)
    if m:
        art = re.search(r"commune (du|de la|de|d')", tete).group(1)
        commune = ("Le " if art == "du" else "La " if art == "de la" else "") + m.group(1).strip()
        out.append(_f("commune", "projet", {"nom": commune, "departement": m.group(2)}, nom, p1, m.group(0)))
    if re.search(r"autorisation de defrichement|l\.\s?341-", nfull[:3000]):
        out.append(_f("type_procedure", "projet", "Autorisation de défrichement (L.341-1 C. for.)",
                      nom, p1, "portant autorisation de défrichement"))
        m = re.search(r"boisement[^.]{0,80}?surface de (\d+(?:,\d+)?) ha", full)
        if m:
            out.append(_f("obligation_boisement", "projet", {"surface_ha": _num(m.group(1))},
                          nom, page_de(r"surface de " + m.group(1).replace(",", ",")), m.group(0)))
        m = re.search(r"indemnit[ée] d.un montant de ([\d \u202f]+) ?€", full)
        if m:
            out.append(_f("indemnite", "projet", _num(m.group(1)), nom, page_de("indemnite d"), m.group(0)))
    elif "l. 411-2" in nfull or "especes animales protegees" in nfull:
        out.append(_f("type_procedure", "projet", "Dérogation espèces protégées (L.411-2 C. env.)",
                      nom, p1, "dérogation aux interdictions de destruction d'espèces protégées"))
    m = re.search(r"dur[ée]e minimum de (\d+) ans", nfull)
    if m:
        out.append(_f("duree_ans", "projet", int(m.group(1)), nom, page_de(r"duree minimum de"), m.group(0)))
    fins = re.findall(r"jusqu.?en (\d{4})", nfull)
    if fins:
        out.append(_f("horizon", "projet", {"fin": max(map(int, fins))}, nom, page_de(r"jusqu.?en " + max(fins)),
                      f"jusqu'en {max(fins)}"))
    m = re.search(r"(?:a compter de|des) (20\d\d) pour les secteurs", nfull)
    if m:
        out.append(_f("ancre_temporelle", "projet", {"etat_zero": int(m.group(1)), "N": None,
                                                      "bloc": "art. 14", "titre": "suivis dès l'état zéro",
                                                      "nature": "obligation"},
                      nom, page_de(r"instaures des"), m.group(0)))

    # obligations surfaciques : phrase par phrase, code mesure le plus proche en amont
    plat = " ".join(full.split())
    phrases = re.split(r"(?<=[.;])\s+(?=[A-ZÉ])", plat)
    dernier_code = None
    for ph in phrases:
        m_code = re.search(r"mesure\s+(MC\s?[\d.]+\s?[a-z]?(?:\s?[-–]\s?\d)?)", ph)
        if m_code:
            dernier_code = re.sub(r"\s", "", m_code.group(1)).replace("–", "-")
        m1 = re.search(r"(\d+(?:,\d+)?) ha minimum, r[ée]partis sur (\d+) parcelles", ph)
        m3 = re.search(r"sur (\d+) secteurs? \((\d+) parcelles\) d.un total de (\d+(?:,\d+)?) ha", ph)
        val = None
        if m1:
            val = {"surface_min_ha": _num(m1.group(1)), "nb_parcelles": int(m1.group(2))}
        elif m3:
            val = {"surface_min_ha": _num(m3.group(3)), "nb_parcelles": int(m3.group(2)),
                   "nb_secteurs": int(m3.group(1))}
        if val and dernier_code:
            ancre = m1 or m3
            i0 = max(0, ancre.start() - 160)
            val["texte"] = ph[i0:ancre.end() + 80]
            out.append(_f("obligation_surface", dernier_code, val, nom,
                          page_de(re.escape(norm(ph[:40]))), val["texte"]))
    # surfaces évitées
    for m in re.finditer(r"en [ée]vitant (\d+(?:,\d+)?) ha de ([\w\s]+?) favorables.*?ainsi que (\d+) m² de (\w+)", " ".join(full.split())):
        out.append(_f("evitement_surface", "projet",
                      [{"surface_ha": _num(m.group(1)), "milieu": m.group(2).strip()},
                       {"surface_ha": int(m.group(3)) / 1e4, "milieu": m.group(4)}],
                      nom, page_de("en evitant"), m.group(0)))
    return out


# ================================================================== tableaux markdown (OCR)
from .cadastre import cle_parcelle  # noqa: E402
from .excel_profil import _annee, _libelle_col, _rang  # noqa: E402
from .mdnorm import remplir_vers_le_bas, tableaux  # noqa: E402

SEUIL_FIABILITE = 0.8


def _num_fr(s: str):
    m = re.search(r"\d{1,3}(?:[ \u202f\u00a0]\d{3})+(?:,\d+)?|\d+(?:,\d+)?", s or "")
    return float(re.sub(r"[ \u202f\u00a0]", "", m.group(0)).replace(",", ".")) if m else None


def _surface(s: str):
    """'2,1 ha' | '3 000 m²' | '19,6 ha (13 parcelles)' | 'Non soumis…'"""
    t = norm(s or "")
    if "non soumis" in t:
        return {"non_soumis": True}
    v = _num_fr(s)
    if v is None:
        return None
    ha = v / 1e4 if re.search(r"m\s*²|m2", s) else v
    m = re.search(r"\((\d+)\s*parcelles?\)", t)
    return {"surface_ha": round(ha, 4), "nb_parcelles": int(m.group(1)) if m else None}


def _ancre_depuis_lignes(rows: list[list[str]]):
    for a, b in zip(rows, rows[1:]):
        ans = [_annee(c) for c in a]
        rgs = [_rang(c) for c in b]
        paires = [(x, y) for x, y in zip(ans, rgs) if x and y is not None]
        if len(paires) >= 5:
            ez = next((x for x, y in paires if y == -1), None)
            n0 = next((x - y for x, y in paires if y >= 0), None)
            if n0 is not None:
                return {"etat_zero": ez if ez else n0 - 1, "N": n0}
    return None


def _mapper_colonnes(t, colonnes: dict):
    """Retrouve, dans les 3 premières lignes du tableau, la ligne d'en-tête qui contient les
    libellés annoncés par la carte. Renvoie (index_ligne_entete, {role: colonne})."""
    rows = [t.entete] + t.lignes[:2]
    for k, r in enumerate(rows):
        m = {}
        for role, lib in colonnes.items():
            if not lib:
                continue
            nl = norm(lib)
            j = next((j for j, c in enumerate(r) if c and (norm(c) == nl or nl in norm(c))), None)
            if j is not None:
                m[role] = j
        if len(m) >= max(1, len([v for v in colonnes.values() if v]) - 1):
            for role, lib in colonnes.items():
                if lib and role not in m:
                    for r2 in rows:
                        j = next((j for j, c in enumerate(r2) if c and norm(lib) in norm(c)), None)
                        if j is not None:
                            m[role] = j
                            break
            return k, m
    return None, {}


def faits_tableaux_carte(doc, carte, regles) -> list[Fait]:
    out, nom = [], doc.nom
    pages = {p.num: p for p in doc.pages}
    for ref in carte.tableaux:
        if ref.role not in ("synthese_actions", "synthese_unites", "synthese_mesures", "parcelles"):
            continue
        cols = {k: v for k, v in ref.colonnes.model_dump().items() if v}
        suite_map = None
        for n in [ref.page] + ref.pages_suite:
            p = pages.get(n)
            if not p or not p.markdown:
                continue
            fiable = p.fiabilite_ocr is None or p.fiabilite_ocr >= SEUIL_FIABILITE
            for t in tableaux(p.markdown, n):
                k, m = _mapper_colonnes(t, cols)
                if k is None and suite_map and len(t.entete) == suite_map[1]:
                    k, m, data = -1, suite_map[0], [t.entete] + t.lignes
                elif k is None:
                    continue
                else:
                    data = ([t.entete] + t.lignes)[k + 1:]
                    suite_map = (m, len(t.entete))
                loc = f"p.{n} tableau {t.index + 1} (carte)"
                def g(r, role, _m=m):
                    return r[_m[role]].strip() if role in _m and _m[role] < len(r) else ""
                if ref.role == "synthese_actions":
                    t.lignes = data
                    remplir_vers_le_bas(t, {j for role, j in m.items() if role != "code"})
                    remplies = t.remplies[0] if t.remplies else set()
                    for i, r in enumerate(t.lignes):
                        code = regles.code(g(r, "code"))
                        if not code:
                            continue
                        fus = (i, m.get("unite")) in remplies
                        if g(r, "unite"):
                            out.append(_f("action_ug", code, regles.unites(g(r, "unite")), nom,
                                          loc + (" (cellule vide complétée)" if fus else ""),
                                          f"{code} → {g(r, 'unite')}", conf=0.7 if fus else 1.0))
                        if g(r, "intitule"):
                            out.append(_f("action_intitule", code, g(r, "intitule"), nom, loc, g(r, "intitule")))
                        if g(r, "cible") and norm(g(r, "cible")) not in ("toutes especes", "-"):
                            out.append(_f("action_cible", code, g(r, "cible"), nom, loc, g(r, "cible")))
                        if g(r, "objectif"):
                            out.append(_f("action_objectif", code, g(r, "objectif"), nom, loc, g(r, "objectif")))
                elif ref.role == "synthese_unites":
                    for r in data:
                        us = regles.unites(g(r, "unite"))
                        if len(us) == 1 and g(r, "intitule"):
                            out.append(_f("ug_libelle", us[0], g(r, "intitule"), nom, loc,
                                          f"{g(r, 'unite')} : {g(r, 'intitule')}"))
                            if g(r, "surface"):
                                sv = _surface(g(r, "surface")) or {}
                                if sv.get("surface_ha"):
                                    out.append(_f("ug_surface_doc", us[0], sv["surface_ha"], nom, loc, g(r, "surface")))
                elif ref.role == "synthese_mesures":
                    for r in data:
                        lib = g(r, "mesure")
                        mm = re.search(r"(?i)(evitement|évitement|reduction|réduction|compensation|accompagnement)\s*[-–]?\s*([\w.-]+)", lib)
                        if not mm:
                            continue
                        sv = _surface(g(r, "surface")) or {}
                        if "surface_ha" in sv and not fiable:
                            sv["douteux"] = True
                        out.append(_f("mesure_plan", mm.group(2).upper(),
                                      {"type_erc": norm(mm.group(1))[0].upper(), "libelle": lib,
                                       "cible": g(r, "cible") or None, **sv},
                                      nom, loc, " | ".join(x for x in r if x)))
                elif ref.role == "parcelles":
                    for r in data:
                        if not re.fullmatch(r"\d{1,4}", g(r, "numero")):
                            continue
                        out.append(_f("parcelle_tableau", re.sub(r"\s+", " ", ref.titre or f"p.{ref.page}")[:90],
                                      {"parcelle": cle_parcelle(g(r, "section"), g(r, "numero"), g(r, "subdivision")),
                                       "surface_ha": _num_fr(g(r, "surface")), "douteux": not fiable},
                                      nom, loc, " | ".join(x for x in r if x)))
                break   # un tableau de référence par page
    return out


def faits_tableaux_md(doc, familles: dict, carte=None, regles=None) -> list[Fait]:
    if carte is not None:
        faits = faits_tableaux_carte(doc, carte, regles)
        return faits + [f for f in _faits_tableaux_heuristiques(doc, familles) if f.type == "ancre_temporelle"]
    return _faits_tableaux_heuristiques(doc, familles)


def _faits_tableaux_heuristiques(doc, familles: dict) -> list[Fait]:
    """Tableaux du markdown Mistral : synthèse des actions, synthèse des mesures,
    parcelles, conventions temporelles. Chaque tableau passe deux garde-fous :
    codes cohérents avec les familles du document, chiffres présents dans la couche native."""
    out, nom = [], doc.nom
    dernier_parc = None
    ancres_vues = set()
    for p in doc.pages:
        if not p.markdown:
            continue
        fiable = p.fiabilite_ocr is None or p.fiabilite_ocr >= SEUIL_FIABILITE
        for t in tableaux(p.markdown, p.num):
            rows = [t.entete] + t.lignes
            loc = f"p.{p.num} tableau {t.index + 1}"
            # --- conventions temporelles (N / état zéro) : pas besoin des chiffres natifs
            anc = _ancre_depuis_lignes(rows)
            if anc and (anc["etat_zero"], anc["N"]) not in ancres_vues:
                ancres_vues.add((anc["etat_zero"], anc["N"]))
                out.append(_f("ancre_temporelle", "projet",
                              {**anc, "bloc": loc, "titre": "frise de périodicité", "nature": "planning"},
                              nom, loc, f"État zéro {anc['etat_zero']} / N = {anc['N']}"))
            if sum(_annee(c) is not None for c in t.entete) >= 5:
                continue  # matrice de planning : passe 3 (et le tableur fait foi)

            libs = [_libelle_col(c) for c in t.entete]
            # --- synthèse code ↔ UG
            if "code" in libs and "ug" in libs:
                jc, ju = libs.index("code"), libs.index("ug")
                codes = [code_norm(r[jc]) if jc < len(r) else None for r in t.lignes]
                ok = [c for c in codes if c and (m := re.match(r"[A-Z]+", c)) and m.group(0) in familles]
                if not codes or len(ok) < 0.8 * len([c for c in codes if c]):
                    out.append(_f("tableau_rejete", loc, "codes incohérents avec les familles du document",
                                  nom, loc, t.titre))
                    continue
                autres = {j for j, l in enumerate(libs) if j != jc}
                remplir_vers_le_bas(t, autres)
                remplies = t.remplies[0] if t.remplies else set()
                for i, (r, c) in enumerate(zip(t.lignes, codes)):
                    if not c:
                        continue
                    fus = (i, ju) in remplies
                    out.append(_f("action_ug", c, ugs_dans(r[ju]), nom, loc + (" (cellule vide complétée)" if fus else ""),
                                  f"{c} → {r[ju]}", conf=0.7 if fus else 1.0))
                    for j, h in enumerate(t.entete):
                        k = norm(h)
                        if j in (jc, ju) or j >= len(r) or not r[j]:
                            continue
                        if "objectif" in k and "long terme" not in k:
                            continue
                        if "operation" in k or "intitule" in k:
                            out.append(_f("action_intitule", c, r[j], nom, loc, r[j]))
                        elif ("espece" in k or "cible" in k) and norm(r[j]) not in ("toutes especes", "-"):
                            out.append(_f("action_cible", c, r[j], nom, loc, r[j]))
                        elif "long terme" in k:
                            out.append(_f("action_objectif", c, r[j], nom, loc, r[j]))
                continue
            # --- synthèse des mesures (Tabl. 1)
            jm = next((j for j, h in enumerate(t.entete) if norm(h) in ("mesure", "mesures")), None)
            js = next((j for j, h in enumerate(t.entete) if "surface" in norm(h)), None)
            if jm is not None and js is not None:
                je = next((j for j, h in enumerate(t.entete) if "espece" in norm(h) or "cible" in norm(h)), None)
                for r in t.lignes:
                    lib = r[jm] if jm < len(r) else ""
                    m = re.search(r"(?i)(evitement|reduction|compensation|accompagnement)\s*[-–]\s*([\w.-]+)", lib)
                    if not m:
                        continue
                    sv = _surface(r[js] if js < len(r) else "")
                    if sv and "surface_ha" in sv and not fiable:
                        sv["douteux"] = True
                    out.append(_f("mesure_plan", m.group(2).upper(),
                                  {"type_erc": m.group(1)[0].upper(), "libelle": lib,
                                   "cible": r[je] if je is not None and je < len(r) else None,
                                   **(sv or {})}, nom, loc, " | ".join(x for x in r if x)))
                continue
            # --- parcelles (Tabl. 2-4, y compris les suites sans en-tête)
            ent = [_libelle_col(c) for c in (t.lignes[0] if t.lignes else [])]
            if "section" in ent and "numero" in ent:
                cols, data = {k: j for j, k in enumerate(ent) if k}, t.lignes[1:]
                cols["surface"] = next((j for j, h in enumerate(t.entete) if "surface" in norm(h)), None)
                titre = t.titre
            elif (dernier_parc and t.entete and re.fullmatch(r"\d{5}", t.entete[0].strip())
                    and len(t.entete) == dernier_parc["n"]):
                cols, data, titre = dernier_parc["cols"], rows, dernier_parc["titre"]
            else:
                continue
            if not (titre or "").lower().startswith("tabl"):
                titre = dernier_parc["titre"] if dernier_parc else titre
            dernier_parc = {"cols": cols, "titre": titre, "n": len(t.entete)}
            for r in data:
                def g(k, _r=r, _cols=cols):
                    return _r[_cols[k]] if _cols.get(k) is not None and _cols[k] < len(_r) else ""
                if not re.fullmatch(r"\d{1,4}", g("numero").strip()):
                    continue
                out.append(_f("parcelle_tableau", re.sub(r"\s+", " ", titre or "")[:90],
                              {"parcelle": cle_parcelle(g("section"), g("numero"), g("subdiv")),
                               "surface_ha": _num_fr(g("surface")), "douteux": not fiable},
                              nom, loc, " | ".join(x for x in r if x)))
    return out
