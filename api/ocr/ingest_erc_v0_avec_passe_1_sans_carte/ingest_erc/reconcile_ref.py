"""Étape 2 — réconciliation du référentiel.

Entrées : faits (tableur, plan, arrêté) + zones SIG candidates.
Sortie  : ReferentielPropose = 3 tables pré-remplies (projet, UG, actions),
          questions pour le BE, contrôles croisés.

Règle de confiance (volontairement simple et explicable) :
  haute   = au moins 2 sources indépendantes concordent (ou attribut SIG explicite
            confirmé par les parcelles)
  moyenne = une seule source explicite, ou inférence cohérente
  basse   = source douteuse (cellule fusionnée, coquille) ou conflit
Toute inférence et tout conflit produit une Question.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .cadastre import correspond
from .inventaire import norm
from .modeles import Cellule, LigneAction, LigneUG, Question, ReferentielPropose, Source

STOP = {"compen", "evitem", "mesure", "faveur", "especes", "espace", "espaces", "maintenir",
        "creer", "entret", "adapte", "restau", "preser", "oiseau", "landic", "toutes", "cortege"}


def premiere_ligne_utile(texte: str) -> str:
    """Première ligne de titre, en ignorant logos markdown et séparateurs."""
    for l in (texte or "").splitlines():
        s = l.strip()
        if not s or s.startswith("!["):
            continue
        if re.fullmatch(r"[.\-_|:=\s]+", s):
            continue
        if len(s) < 8:
            continue
        return s
    return ""


def stems(txt: str) -> set:
    return {w[:6] for w in re.findall(r"[a-z]{4,}", norm(re.sub(r"[_]", " ", txt or "")))} - STOP


def recouvrement(a: str, b: str) -> float:
    A, B = stems(a), stems(b)
    return len(A & B) / max(1, min(len(A), len(B)))


class Ctx:
    def __init__(self, faits, zones, couches, docs):
        self.faits = faits
        self.zones = zones
        self.couches = couches
        self.docs = docs
        self.q: list[Question] = []
        self.controles: list[dict] = []
        self.idx = defaultdict(list)
        for f in faits:
            self.idx[(f.type, f.cle)].append(f)

    def get(self, type_, cle=None):
        if cle is None:
            fs = [f for f in self.faits if f.type == type_]
        else:
            fs = list(self.idx.get((type_, cle), []))
        users = [f for f in fs if f.methode == "user"]
        return users or fs

    def question(self, portee, texte, options=(), proposition=None, sources=()):
        self.q.append(Question(id=f"Q{len(self.q) + 1}", portee=portee, texte=texte,
                               options=list(options), proposition=proposition,
                               sources=list(sources)))

    def controle(self, nom, ok, detail, niveau=None):
        self.controles.append({"controle": nom, "statut": "ok" if ok else (niveau or "alerte"),
                               "detail": detail})


# ================================================================== projet
def _projet(cx: Ctx) -> dict[str, Cellule]:
    P = {}

    def simple(cle, type_, transfo=lambda v: v, confirme_par=None):
        fs = cx.get(type_, "projet")
        if not fs:
            return
        v = transfo(fs[0].valeur)
        srcs = [fs[0].source]
        conf = "moyenne"
        if confirme_par:
            s2 = confirme_par(v)
            if s2:
                srcs.append(s2)
                conf = "haute"
        P[cle] = Cellule(valeur=v, confiance=conf, sources=srcs)

    plan = next((d for d in cx.docs if d.role == "plan_gestion"), None)

    def dans_plan(motif):
        if not plan:
            return None
        for p in plan.pages:
            m = re.search(motif, norm(p.texte))
            if m:
                return Source(doc=plan.nom, loc=f"p.{p.num}", extrait=m.group(0)[:120])
        return None

    # nom : libellé de l'arrêté, confirmé par le titre du plan de gestion
    lib = cx.get("projet_libelle", "projet")
    titre_pg = premiere_ligne_utile(plan.pages[0].texte) if plan and plan.pages else ""
    if lib:
        v = lib[0].valeur
        temoins = [titre_pg] + (plan.lignes_repetees if plan else [])
        ok = any(t and recouvrement(v, t) >= 0.5 for t in temoins)
        P["nom"] = Cellule(valeur=v, confiance="haute" if ok else "moyenne",
                           sources=[lib[0].source] + ([Source(doc=plan.nom, loc="p.1", extrait=titre_pg)] if ok else []))
    elif plan and plan.lignes_repetees:
        pied = max(plan.lignes_repetees, key=len)
        P["nom"] = Cellule(valeur=pied, confiance="moyenne",
                           sources=[Source(doc=plan.nom, loc="pied de page", extrait=pied)])
    elif titre_pg:
        P["nom"] = Cellule(valeur=titre_pg.capitalize(), confiance="moyenne",
                           sources=[Source(doc=plan.nom, loc="p.1", extrait=titre_pg)],
                           note="Titre du plan de gestion — à raccourcir si besoin")
    simple("maitre_ouvrage", "maitre_ouvrage", lambda v: re.sub(r"^la\s+", "", v, flags=re.I),
           confirme_par=lambda v: dans_plan(re.escape(norm(v))))
    simple("reference_decision", "decision_reference")
    mois = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
            "septembre", "octobre", "novembre", "decembre"]
    simple("date_decision", "decision_date",
           confirme_par=lambda v: dans_plan(rf"{int(v[8:])}(er)? {mois[int(v[5:7]) - 1]} {v[:4]}"))
    simple("type_procedure", "type_procedure")
    # commune : arrêté + code INSEE majoritaire des parcelles citées
    fc = cx.get("commune", "projet")
    insee = Counter(r["commune"] for f in cx.get("action_parcelles_pdf") for r in f.valeur if r["commune"])
    if fc:
        srcs = [fc[0].source]
        val = dict(fc[0].valeur)
        if insee:
            code, n = insee.most_common(1)[0]
            if code.startswith(val["departement"]):
                val["insee"] = code
                srcs.append(Source(doc=cx.get("action_parcelles_pdf")[0].source.doc,
                                   loc="parcelles citées", extrait=f"{n} références en {code}"))
        P["commune"] = Cellule(valeur=val, confiance="haute" if len(srcs) > 1 else "moyenne", sources=srcs)
        for code, n in insee.items():
            if code != val.get("insee"):
                cx.controle("Code commune des parcelles", False,
                            f"{n} référence(s) avec la commune « {code} » (coquille probable de "
                            f"{val.get('insee')}) — corrigé automatiquement", "info")

    # horizon et durée
    fins = [(f.valeur["fin"], f.source) for f in cx.get("horizon", "projet") if f.valeur.get("fin")]
    if fins:
        vals = Counter(v for v, _ in fins)
        v, n = vals.most_common(1)[0]
        P["annee_fin"] = Cellule(valeur=v, confiance="haute" if n > 1 else "moyenne",
                                 sources=[s for x, s in fins if x == v])
    duree = cx.get("duree_ans", "projet")
    debuts = [f.valeur["debut"] for f in cx.get("horizon", "projet") if f.valeur.get("debut")]

    # ancre temporelle : vote pondéré, conflit => question
    votes = defaultdict(list)
    for f in cx.get("ancre_temporelle", "projet"):
        if f.valeur.get("etat_zero"):
            votes[f.valeur["etat_zero"]].append(f)
    for d, f in zip(debuts, [f for f in cx.get("horizon", "projet") if f.valeur.get("debut")]):
        votes[d].append(f)
    if votes:
        # poids : 1 par document distinct + 0.25 par bloc supplémentaire du même document
        def poids(fs):
            docs = Counter(f.source.doc for f in fs)
            return sum(1 + 0.25 * (n - 1) for n in docs.values())
        classes = sorted(votes.items(), key=lambda kv: -poids(kv[1]))
        best, fs = classes[0]
        conf = "haute" if len(classes) == 1 and len({f.source.doc for f in fs}) > 1 else \
               "basse" if len(classes) > 1 else "moyenne"
        P["annee_etat_zero"] = Cellule(valeur=best, confiance=conf, sources=[f.source for f in fs][:6])
        P["annee_N"] = Cellule(valeur=best + 1, confiance=conf,
                               sources=[f.source for f in fs if f.valeur.get("N") == best + 1][:3],
                               note="Année N = première année de gestion après l'état zéro")
        if len(classes) > 1:
            def resume(fs2):
                par_doc = Counter(f.source.doc for f in fs2)
                return ", ".join(f"{d}" + (f" ({n} tableaux)" if n > 1 else "") for d, n in par_doc.items())
            detail = " ; ".join(f"{an} selon {resume(fs2)}" for an, fs2 in classes)
            cx.question("projet",
                        "Année de l'état zéro : les documents divergent. " + detail,
                        [str(a) for a, _ in classes], str(best),
                        [f.source for _, fs2 in classes for f in fs2[:2]])
    if duree:
        srcs = [duree[0].source]
        conf = "moyenne"
        if "annee_fin" in P and "annee_etat_zero" in P and \
                P["annee_fin"].valeur - P["annee_etat_zero"].valeur == duree[0].valeur:
            conf = "haute"
            srcs.append(Source(doc="(calcul)", loc="fin − état zéro",
                               extrait=f"{P['annee_fin'].valeur} − {P['annee_etat_zero'].valeur}"))
        P["duree_ans"] = Cellule(valeur=duree[0].valeur, confiance=conf, sources=srcs)

    # convention des montants
    co = cx.get("coef_totaux", "projet")
    if co:
        k = co[0].valeur["k"]
        P["convention_montants"] = Cellule(
            valeur=f"Lignes HT, totaux × {k}", confiance="basse", sources=[co[0].source],
            note=f"{co[0].valeur['scores'][k]} totaux sur {co[0].valeur['nb_totaux']} = montant × {k}")
        cx.question("projet",
                    f"Les totaux du tableur valent les montants × {k} (TVA {round((k - 1) * 100, 1)} % ?). "
                    "Les montants par ligne sont-ils HT ?",
                    ["Lignes HT, totaux TTC", "Tout est TTC", "Tout est HT (le coefficient est autre chose)"],
                    "Lignes HT, totaux TTC", [co[0].source])
    return P


# ================================================================== actions
def _actions(cx: Ctx, familles: dict) -> tuple[list[LigneAction], dict]:
    ignore = {f.cle for f in cx.get("action_supprime")}
    codes = sorted({f.cle for f in cx.faits
                    if f.type in ("action_ug", "action_intitule") and f.cle not in ignore},
                   key=lambda c: (re.sub(r"\d.*", "", c) or c, int(re.sub(r"\D", "", c) or 0)))
    lignes, ug_par_code = [], {}
    fusions = defaultdict(list)
    for c in codes:
        # intitulé
        its = cx.get("action_intitule", c)
        par_doc = {}
        for f in its:
            par_doc.setdefault(f.source.doc, f)
        its = list(par_doc.values())
        if len(its) >= 2 and recouvrement(its[0].valeur, its[1].valeur) >= 0.6:
            it = Cellule(valeur=min((f.valeur for f in its), key=len), confiance="haute",
                         sources=[f.source for f in its])
        elif its:
            it = Cellule(valeur=its[0].valeur, confiance="moyenne", sources=[f.source for f in its],
                         note=None if len(its) == 1 else "Intitulés différents selon les documents")
        else:
            it = Cellule(valeur=None, confiance="basse")
        # nature (famille, ou saisie BE)
        fn = cx.get("action_nature", c)
        pre = re.match(r"[A-Z]+", c)
        pre = pre.group(0) if pre else ""
        fam = familles.get(pre)
        if fn:
            nat = Cellule(valeur=fn[0].valeur, confiance="haute" if fn[0].methode == "user" else "moyenne",
                          sources=[fn[0].source])
        else:
            nat = Cellule(valeur=fam.valeur if fam else None,
                          confiance="haute" if fam else "basse",
                          sources=[fam.source] if fam else [])
        # UG
        fu = [f for f in cx.get("action_ug", c) if f.valeur]
        par_doc = defaultdict(list)
        for f in fu:
            val = f.valeur if isinstance(f.valeur, list) else []
            if val:
                par_doc[f.source.doc].append(f)
        ensembles = {d: fs[0] for d, fs in par_doc.items()}
        valeurs = {tuple(sorted(f.valeur)) for f in ensembles.values()}
        douteux = [f for f in ensembles.values() if f.confiance_extraction < 1]
        if len(ensembles) >= 2 and len(valeurs) == 1 and len(douteux) == len(ensembles):
            f = next(iter(ensembles.values()))
            ug = Cellule(valeur=f.valeur, confiance="basse", sources=[x.source for x in ensembles.values()],
                         note="Cellule fusionnée dans toutes les sources")
            fusions[", ".join(f.valeur)].append((c, it.valeur, f.source))
        elif len(ensembles) >= 2 and len(valeurs) == 1:
            ug = Cellule(valeur=list(valeurs.pop()), confiance="haute",
                         sources=[f.source for f in ensembles.values()])
        elif len(valeurs) > 1:
            opts = [", ".join(v) for v in valeurs]
            ug = Cellule(valeur=list(sorted(valeurs, key=len)[0]), confiance="basse",
                         sources=[f.source for f in ensembles.values()], note="Conflit entre documents")
            cx.question(c, f"{c} : UG différentes selon les documents ({' / '.join(opts)}).",
                        opts, opts[0], [f.source for f in ensembles.values()])
        elif ensembles:
            f = next(iter(ensembles.values()))
            ug = Cellule(valeur=f.valeur, confiance="basse" if douteux else "moyenne",
                         sources=[f.source],
                         note="Lu dans une cellule fusionnée" if douteux else "Une seule source")
            if douteux:
                fusions[", ".join(f.valeur)].append((c, it.valeur, f.source))
        else:
            ug = Cellule(valeur=[], confiance="basse", note="Aucune UG trouvée")
            cx.question(c, f"{c} ({it.valeur}) : aucune UG trouvée. Sur quelles UG porte cette action ?")
        ug_par_code[c] = ug.valeur
        # cible
        fcib = cx.get("action_cible", c)
        cib = Cellule(valeur=fcib[0].valeur if fcib else None,
                      confiance="moyenne" if fcib else "basse",
                      sources=[fcib[0].source] if fcib else [])
        lignes.append(LigneAction(code=c, intitule=it, nature=nat, ugs=ug, cible=cib))
    for val, items in fusions.items():
        cx.question(", ".join(c for c, _, _ in items),
                    "Dans le tableur, une même cellule UG fusionnée (" + val + ") couvre plusieurs actions : "
                    + " ; ".join(f"{c} ({i})" for c, i, _ in items)
                    + ". Préciser les UG de chacune (pré-rempli : toutes).",
                    [val], val, [s for _, _, s in items])
    return lignes, ug_par_code


def _blocs_incoherents(cx: Ctx, familles: dict, ug_par_code: dict, parc_ug: dict) -> set:
    """Bloc dont le titre évoque une famille (ex. « entretien ») mais codé avec une autre."""
    suspects = set()
    for f in cx.get("bloc_codes"):
        titre = norm(f.valeur["titre"])
        fam_codes = {re.match(r"[A-Z]+", c).group(0) for c in f.valeur["codes"]}
        for pre, ff in familles.items():
            autres = {w for p2, f2 in familles.items() if p2 != pre for w in re.findall(r"[a-z]{6,}", norm(f2.valeur))}
            mots = [w for w in re.findall(r"[a-z]{6,}", norm(ff.valeur)) if w not in autres]
            if not any(w[:7] in titre for w in mots) or pre in fam_codes:
                continue
            ugs = set()
            for p in f.valeur["parcelles"]:
                ugs |= parc_ug.get(p, set())
            cands = [c for c, u in ug_par_code.items() if c.startswith(pre) and set(u) >= ugs and ugs]
            cands.sort(key=lambda c: len(ug_par_code[c]))
            prop = cands[0] if cands else None
            cx.question(f.cle, f"Le bloc « {f.valeur['titre']} » ({f.source.doc}) est codé "
                               f"{', '.join(f.valeur['codes'])} alors que son titre correspond à la famille "
                               f"{pre} ({ff.valeur}). Ses parcelles relèvent de {', '.join(sorted(ugs)) or '?'}."
                               + (f" S'agit-il de {prop} ?" if prop else ""),
                        ([prop] if prop else []) + ["Garder " + ", ".join(f.valeur["codes"])],
                        prop, [f.source])
            suspects.add(f.cle)
    # un seul message par titre de bloc (versions planning / coûts du même tableau)
    vus, uniq = {}, []
    for q in cx.q:
        k = re.sub(r"\(.*?\)", "", q.texte) if q.portee in suspects else q.id
        if k in vus:
            vus[k].sources += q.sources
            vus[k].portee += f", {q.portee}"
            continue
        vus[k] = q
        uniq.append(q)
    cx.q = uniq
    return suspects


# ================================================================== UG ↔ zones
def _ugs(cx: Ctx, ug_par_code: dict, actions: list[LigneAction], suspects: set):
    ug_codes = sorted({u for us in ug_par_code.values() for u in us} |
                      {f.cle for f in cx.get("ug_libelle")} |
                      {z.ug_attribut for z in cx.zones if z.ug_attribut},
                      key=lambda u: int(re.sub(r"\D", "", u) or 0))
    suppr = {f.cle for f in cx.get("ug_supprime")}
    ug_codes = [u for u in ug_codes if u and u not in suppr]
    lib = {u: cx.get("ug_libelle", u) for u in ug_codes}
    cibles_ug = defaultdict(str)
    for a in actions:
        for u in a.ugs.valeur or []:
            cibles_ug[u] += " " + (a.cible.valeur or "") + " " + (a.intitule.valeur or "")

    # parcelles et surfaces attendues par UG (codes mono-UG, blocs cohérents seulement)
    attendu_refs, attendu_surf = defaultdict(set), defaultdict(dict)
    multi_refs = defaultdict(set)
    for f in cx.get("code_parcelle"):
        if f.valeur["bloc"] in suspects:
            continue
        us = ug_par_code.get(f.cle, [])
        if len(us) == 1:
            attendu_refs[us[0]].add(f.valeur["parcelle"])
            attendu_surf[us[0]][(f.valeur["parcelle"], f.valeur["surface_ha"])] = f.valeur["surface_ha"] or 0
    for f in cx.get("action_parcelles_pdf"):
        us = ug_par_code.get(f.cle, [])
        cles = {r["cle"] for r in f.valeur}
        if len(us) == 1:
            attendu_refs[us[0]] |= cles
        for u in us:
            multi_refs[u] |= cles
    evit = [e for f in cx.get("evitement_surface") for e in f.valeur]

    def type_label(u):
        l = norm(lib[u][0].valeur) if lib[u] else ""
        return "E" if "evit" in l else None

    def ref_overlap(zrefs, refs):
        if not zrefs or not refs:
            return 0.0
        return sum(any(correspond(z, r) for r in refs) for z in zrefs) / len(zrefs)

    assign, lignes = {}, []
    # 1) attribut explicite
    for z in cx.zones:
        if z.ug_attribut:
            ov = ref_overlap(z.refs_cadastrales, attendu_refs[z.ug_attribut] | multi_refs[z.ug_attribut])
            assign[z.ug_attribut] = (z, "attribut", ov)
    # 2) inférence pour les zones restantes
    libres = [z for z in cx.zones if not z.ug_attribut]
    restant = [u for u in ug_codes if u not in assign]
    scores = []
    for z in libres:
        for u in restant:
            nom_z = stems(z.couche)
            s_nom = len(nom_z & (stems(lib[u][0].valeur if lib[u] else "") | stems(cibles_ug[u])))
            s_ref = ref_overlap(z.refs_cadastrales, attendu_refs[u] | multi_refs[u])
            exp = sum(attendu_surf[u].values())
            s_surf = 0.0
            if exp:
                s_surf = max(0.0, 1 - abs(z.surface_ha - exp) / exp * 5)
            elif any(abs(z.surface_ha - e["surface_ha"]) / e["surface_ha"] < 0.1 for e in evit):
                s_surf = 0.5
            tl = type_label(u)
            s_type = 0.0 if not z.type_erc_nom else (1.0 if (tl or "C") == z.type_erc_nom else -3.0)
            total = 1.5 * s_nom + 2 * s_ref + 1.5 * s_surf + s_type
            preuves = {"nom": s_nom, "parcelles": round(s_ref, 2), "surface": round(s_surf, 2), "type": s_type}
            if s_nom + s_ref + s_surf > 0:
                scores.append((total, z, u, preuves))
    pris_z = set()
    for total, z, u, pr in sorted(scores, key=lambda t: -t[0]):
        if total < 2.5 or u in assign or z.id in pris_z:
            continue
        assign[u] = (z, "inference", pr)
        pris_z.add(z.id)

    for u in ug_codes:
        l = lib[u]
        libelle = Cellule(valeur=l[0].valeur if l else None, confiance="moyenne" if l else "basse",
                          sources=[f.source for f in l])
        z_info = assign.get(u)
        tl = type_label(u)
        if z_info:
            z, mode, pr = z_info
            zs = Source(doc="SIG", loc=z.id, extrait=f"{z.nb_entites} entité(s), {z.surface_ha} ha")
            if mode == "attribut":
                conf = "haute" if pr >= 0.5 else "moyenne"
                zone = Cellule(valeur=z.id, confiance=conf, sources=[zs],
                               note=f"Attribut « {list(z.filtre)[0]} » ; {int(pr * 100)} % des parcelles "
                                    "retrouvées dans les documents")
            else:
                zone = Cellule(valeur=z.id, confiance="moyenne", sources=[zs],
                               note="Déduit : " + ", ".join(f"{k} {v}" for k, v in pr.items() if v))
                cx.question(u, f"{u} ({libelle.valeur}) : la couche « {z.couche} » n'a pas d'attribut UG. "
                               f"Rapprochement proposé d'après {', '.join(k for k, v in pr.items() if v and v > 0)}. "
                               "Confirmer ?", ["Oui", "Autre couche", "Pas de géométrie"], "Oui", [zs])
            typ_v = z.type_erc_nom or tl
            typ = Cellule(valeur=typ_v, confiance="haute" if (tl or "C") == z.type_erc_nom else "moyenne",
                          sources=[zs] + [f.source for f in l])
            exp = round(sum(attendu_surf[u].values()), 2)
            if exp:
                ecart = abs(z.surface_ha - exp) / exp
                surf = Cellule(valeur=z.surface_ha, confiance="haute" if ecart < 0.03 else "moyenne",
                               sources=[zs, Source(doc="tableur", loc=f"somme des parcelles {u}",
                                                   extrait=f"{exp} ha")],
                               note=None if ecart < 0.03 else f"Écart {round(ecart * 100)} % avec le tableur ({exp} ha)")
                cx.controle(f"Surface {u} SIG / tableur", ecart < 0.03,
                            f"{z.surface_ha} ha (SIG) vs {exp} ha (tableur)")
            else:
                surf = Cellule(valeur=z.surface_ha, confiance="moyenne", sources=[zs])
        else:
            zone = Cellule(valeur=None, confiance="basse", note="Aucune géométrie rapprochée")
            typ = Cellule(valeur=tl, confiance="moyenne" if tl else "basse", sources=[f.source for f in l])
            surf = Cellule(valeur=None, confiance="basse")
            cx.question(u, f"{u} ({libelle.valeur}) : aucune géométrie trouvée dans le SIG. "
                           "Quelle couche/entité la représente ?", [z.id for z in libres], None)
        fu = cx.get("ug_zone_sig", u)
        if fu:
            zone = Cellule(valeur=fu[0].valeur, confiance="haute",
                           sources=[fu[0].source], note="Modifié par vous")
        ft = cx.get("ug_type_erc", u)
        if ft:
            typ = Cellule(valeur=ft[0].valeur, confiance="haute",
                          sources=[ft[0].source], note="Modifié par vous")
        fs = cx.get("ug_surface", u)
        if fs:
            surf = Cellule(valeur=fs[0].valeur, confiance="haute",
                           sources=[fs[0].source], note="Modifié par vous")
        lignes.append(LigneUG(ug_code=u, libelle=libelle, type_erc=typ, zone_sig=zone, surface_ha=surf))

    mesures = cx.get("mesure_plan")
    for z in cx.zones:
        if not any(v[0].id == z.id for v in assign.values()):
            nz = stems(z.couche)
            m = max(mesures, key=lambda f: len(nz & (stems(f.valeur.get("cible") or "") | stems(f.valeur["libelle"]))),
                    default=None)
            if m and m.valeur.get("non_soumis") and m.valeur["type_erc"] == (z.type_erc_nom or "") \
                    and nz & stems(m.valeur.get("cible") or ""):
                cx.controle(f"Couche {z.couche}", True,
                            f"= mesure {m.cle} ({m.valeur['cible']}) : non soumise à obligation de résultats, "
                            f"hors plan de gestion ({m.source.doc} {m.source.loc}) → couche de contexte", "info")
                continue
            couche = next((c for c in cx.couches if c.nom == z.couche), None)
            extra = f" (reprojetée depuis EPSG:{couche.epsg})" if couche and couche.reprojetee else ""
            cx.question("SIG", f"La couche « {z.couche} »{extra}, {z.surface_ha} ha, type {z.type_erc_nom or '?'}, "
                               "ne correspond à aucune UG du plan de gestion. Que représente-t-elle ?",
                        ["Évitement sans gestion (contexte)", "Nouvelle UG", "Ignorer"],
                        "Évitement sans gestion (contexte)" if z.type_erc_nom == "E" else None,
                        [Source(doc="SIG", loc=z.id)])
    return lignes, assign


# ================================================================== contrôles
def _parcelles(cx: Ctx, ugs, assign):
    refs_sig = {r for z in cx.zones for r in z.refs_cadastrales}
    refs_xl = {f.valeur["parcelle"] for f in cx.get("code_parcelle")}
    connues = refs_sig | refs_xl
    vus = set()
    for f in cx.get("action_parcelles_pdf"):
        for r in f.valeur:
            if r["cle"] in vus:
                continue
            vus.add(r["cle"])
            if r["cle"] in connues:
                continue
            if r["partielle"]:
                m = [k for k in connues if correspond(r["cle"], k)]
                cx.controle("Référence cadastrale incomplète", bool(m),
                            f"« {r['brut']} » ({f.source.loc}) → {', '.join(m) or 'introuvable'}", "info")
                continue
            voisins = [k for k in connues if re.sub(r"[a-z]$", "", k) == re.sub(r"[a-z]$", "", r["cle"])]
            cx.question(f.cle, f"Parcelle « {r['brut']} » citée dans la fiche {f.cle} ({f.source.loc}) "
                               f"introuvable dans le SIG et le tableur."
                               + (f" Les documents contiennent {', '.join(voisins)} : même parcelle ?" if voisins else ""),
                        voisins + ["Parcelle à ajouter"], voisins[0] if voisins else None, [f.source])


def _obligations(cx: Ctx, actions, ugs_lignes, assign):
    out = []
    type_ug = {l.ug_code: l.type_erc.valeur for l in ugs_lignes}
    especes = set()
    for a in actions:
        for e in re.split(r"\bet\b|,", a.cible.valeur or ""):
            if len(e.strip()) > 6:
                especes.add(e.strip())
    for f in cx.get("obligation_surface"):
        txt = f.valeur["texte"]
        esp = [e for e in especes if norm(e) in norm(txt)]
        if esp:
            codes = [a for a in actions if any(norm(e) in norm(a.cible.valeur or "") for e in esp)]
            base = f"espèce : {', '.join(esp)}"
        else:
            sc = [(len(stems(txt) & stems(a.intitule.valeur or "")), a) for a in actions]
            mx = max(s for s, _ in sc)
            codes = [a for s, a in sc if s == mx and mx >= 2]
            base = f"intitulé : {', '.join(a.code for a in codes)}"
        ugs = sorted({u for a in codes for u in (a.ugs.valeur or []) if type_ug.get(u) == "C"})
        zs = [assign[u][0] for u in ugs if u in assign]
        surf = round(sum(z.surface_ha for z in zs), 2)
        nb = sum(z.nb_entites for z in zs)
        ok_s = surf >= f.valeur["surface_min_ha"] * 0.98
        ok_n = nb == f.valeur["nb_parcelles"]
        out.append({"mesure": f.cle, "surface_min_ha": f.valeur["surface_min_ha"],
                    "nb_parcelles": f.valeur["nb_parcelles"], "ugs": ugs, "rattachement": base,
                    "surface_sig_ha": surf, "nb_entites_sig": nb, "source": f.source.model_dump()})
        cx.controle(f"Obligation {f.cle}", ok_s and ok_n,
                    f"{f.valeur['surface_min_ha']} ha min / {f.valeur['nb_parcelles']} parcelles (arrêté) — "
                    f"SIG {', '.join(ugs)} : {surf} ha / {nb} entités"
                    + ("" if ok_s else " — surface insuffisante") + ("" if ok_n else " — nb de parcelles différent")
                    + (" (tolérance 2 % appliquée)" if ok_s and surf < f.valeur["surface_min_ha"] else ""))
    for f in cx.get("evitement_surface"):
        for e in f.valeur:
            zs = [z for z in cx.zones if z.type_erc_nom == "E" and abs(z.surface_ha - e["surface_ha"]) / e["surface_ha"] < 0.1]
            cx.controle(f"Évitement « {e['milieu']} »", bool(zs),
                        f"{e['surface_ha']} ha (arrêté) — " +
                        (", ".join(f"{z.couche} {z.surface_ha} ha" for z in zs) or "aucune couche d'évitement de cette surface"))
    return out


def _mesures_plan(cx: Ctx, actions, ugs_lignes, assign):
    type_ug = {l.ug_code: l.type_erc.valeur for l in ugs_lignes}
    tab = defaultdict(dict)
    for f in cx.get("parcelle_tableau"):
        tab[f.cle][f.valeur["parcelle"] + str(f.valeur["surface_ha"])] = f.valeur["surface_ha"] or 0
    for f in cx.get("mesure_plan"):
        v = f.valeur
        if v.get("non_soumis") or not v.get("surface_ha"):
            continue
        cible = norm(v.get("cible") or "")
        esp = cible.split(" et ")[0]
        ugs = sorted({u for a in actions if esp and esp in norm(a.cible.valeur or "")
                      for u in (a.ugs.valeur or []) if type_ug.get(u) == v["type_erc"]})
        sig = round(sum(assign[u][0].surface_ha for u in ugs if u in assign), 2)
        t = next((k for k in tab if esp and esp in norm(k)), None)
        somme = round(sum(tab[t].values()), 2) if t else None
        tol = max(0.03 * v["surface_ha"], 0.05)   # 3 % ou 5 ares (arrondis des documents)
        ecarts = [x for x in (sig or None, somme) if x and abs(x - v["surface_ha"]) > tol]
        detail = (f"{v['surface_ha']} ha annoncés ({f.source.loc})"
                  + (f" — tableau de parcelles {somme} ha" if somme else "")
                  + (f" — SIG {', '.join(ugs)} {sig} ha" if ugs else ""))
        cx.controle(f"Mesure {f.cle} du plan de gestion", not ecarts,
                    detail + (" — incohérence interne au dossier, à signaler au BE" if ecarts else ""))


def _documents(cx: Ctx):
    for d in cx.docs:
        for a in d.sous_documents:
            if a.get("doublon_de"):
                cx.controle("Document embarqué", True,
                            f"{d.nom} annexe {a['num']} (p.{a['page_debut']}-{a['page_fin']}) = {a['doublon_de']} "
                            f"({a['methode_doublon']}) → ignorée", "info")
            elif a.get("role_probable") == "arrete":
                cle = f"annexe{a['num']}"
                info = {f.type: f.valeur for f in cx.faits if f.cle == cle}
                resume = ", ".join(x for x in [
                    info.get("decision_reference"), info.get("type_procedure"),
                    f"du {info['decision_date']}" if info.get("decision_date") else None,
                    f"reboisement {info['obligation_boisement']['surface_ha']} ha" if info.get("obligation_boisement") else None,
                    f"indemnité {int(info['indemnite']):,} €".replace(",", " ") if info.get("indemnite") else None,
                ] if x)
                cx.question("documents", f"L'annexe {a['num']} du plan de gestion contient une autre décision"
                                         + (f" ({resume})" if resume else f" (« {a['titre'][:70]} »)")
                                         + (", en pages scannées" if a.get("pages_scannees") and not resume else "")
                                         + ", non fournie séparément. L'ajouter comme décision du projet ?",
                            ["Oui, l'extraire de l'annexe", "Je le dépose séparément", "Non pertinent"],
                            "Oui, l'extraire de l'annexe")
        for w in d.avertissements:
            cx.controle("Document", False, f"{d.nom} : {w}", "info")
    for c in cx.couches:
        for w in c.avertissements:
            cx.controle(f"SIG {c.nom}", True, w, "info")
    for f in cx.get("legende_conflit"):
        cx.question("tableur", f"Le tableur utilise deux abréviations pour « {f.valeur['libelle']} » : "
                               f"{' et '.join(f.valeur['codes'])}. Même opération ?",
                    ["Oui, même opération", "Non, opérations différentes"], "Oui, même opération", [f.source])


# ================================================================== point d'entrée
def reconcilier(faits, zones, couches, docs) -> ReferentielPropose:
    cx = Ctx(faits, zones, couches, docs)
    familles = {}
    for f in cx.get("famille_code"):
        familles.setdefault(f.cle, f)
    projet = _projet(cx)
    actions, ug_par_code = _actions(cx, familles)
    # parcelle -> UG (attributs SIG + codes mono-UG)
    parc_ug = defaultdict(set)
    for z in zones:
        if z.ug_attribut:
            for r in z.refs_cadastrales:
                parc_ug[r].add(z.ug_attribut)
    suspects = _blocs_incoherents(cx, familles, ug_par_code, parc_ug)
    ugs, assign = _ugs(cx, ug_par_code, actions, suspects)
    _parcelles(cx, ugs, assign)
    obligations = _obligations(cx, actions, ugs, assign)
    _mesures_plan(cx, actions, ugs, assign)
    _documents(cx)

    # préparation GéoMCE (indicatif : champs socle disponibles)
    requis = ["nom", "maitre_ouvrage", "commune", "type_procedure", "reference_decision",
              "date_decision", "duree_ans"]
    remplis = sum(k in projet for k in requis) + sum(1 for u in ugs if u.zone_sig.valeur and u.type_erc.valeur)
    total = len(requis) + len(ugs)
    cellules = [c for c in projet.values()] + \
               [c for u in ugs for c in (u.libelle, u.type_erc, u.zone_sig, u.surface_ha)] + \
               [c for a in actions for c in (a.intitule, a.nature, a.ugs, a.cible)]
    stats = {"cellules": len(cellules),
             "confiance": dict(Counter(c.confiance for c in cellules)),
             "questions": len(cx.q),
             "faits": len(faits),
             "geomce_pret_pct": round(100 * remplis / total)}
    return ReferentielPropose(projet=projet, ugs=ugs, actions=actions,
                              obligations_surfaciques=obligations, questions=cx.q,
                              controles=cx.controles, stats=stats)
