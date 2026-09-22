"""Étape 2 — réconciliation du référentiel.

Entrées : faits (tableur, plan, arrêté, + faits user de l'étape 2.5) + zones SIG candidates.
Sortie  : ReferentielPropose = 3 tables pré-remplies (projet, UG, actions),
          questions pour le BE, contrôles croisés, décisions pour la passe 3.

Règle de confiance (volontairement simple et explicable) :
  haute   = au moins 2 sources indépendantes concordent (ou attribut SIG explicite
            confirmé par les parcelles), ou valeur validée / saisie par le BE
  moyenne = une seule source explicite, ou inférence cohérente
  basse   = source douteuse (cellule fusionnée, coquille) ou conflit
Toute inférence et tout conflit produit une Question.
Réponse libre (hors options) : la question est close, rien n'est écrit, le texte part
dans decisions["commentaires"] avec sa portée, comme contexte de la passe 3.

Réinjection (étape 2.5) : chaque question a un id stable "regle:portee".
`cx.question(...)` renvoie la réponse du BE si elle existe (la question n'est alors
pas reposée et la règle applique la réponse), CORRIGE si le BE a directement corrigé
la ou les cellules visées, sinon None (question posée). Les corrections de cellules
sont appliquées en dernier et priment sur tout.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .cadastre import correspond
from .inventaire import norm
from .modeles import Cellule, LigneAction, LigneUG, Question, ReferentielPropose, Source
from .reinjection import LIBRE, appliquer_renommages

STOP = {"compen", "evitem", "mesure", "faveur", "especes", "espace", "espaces", "maintenir",
        "creer", "entret", "adapte", "restau", "preser", "oiseau", "landic", "toutes", "cortege"}

CORRIGE = "__corrige__"     # la question est résolue par une correction directe de cellule


# ================================================================== utilitaires
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


def origine(f) -> str:
    """Source indépendante = document + nature de l'emplacement + méthode."""
    nature = "tableau" if "tableau" in f.source.loc or "!" in f.source.loc else "texte"
    return f"{f.source.doc}#{nature}#{f.methode}"


def recouvrement(a: str, b: str) -> float:
    A, B = stems(a), stems(b)
    return len(A & B) / max(1, min(len(A), len(B)))


def src_be(extrait=None, loc: str = "écran de validation") -> Source:
    return Source(doc="BE", loc=loc, extrait=None if extrait is None else str(extrait)[:120])


def cellule_be(valeur, sources=(), note: str = "Validé par vous") -> Cellule:
    return Cellule(valeur=valeur, confiance="haute", sources=list(sources) + [src_be(valeur)], note=note)


def _annee(v):
    m = re.search(r"\b(?:19|20)\d{2}\b", str(v if v is not None else ""))
    return int(m.group(0)) if m else None


def _code(s) -> str:
    """Normalisation légère d'un code saisi : « UG 5 » -> « UG5 », « TE 1 » -> « TE1 »."""
    s = str(s).strip()
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d+\w*)", s)
    return f"{m.group(1)}{m.group(2)}" if m else s


def _liste(v) -> list[str]:
    """Liste de codes depuis une liste ou un texte « UG1, UG 2 ; UG3 » (éléments vides retirés)."""
    items = v if isinstance(v, list) else re.split(r"[,;]", str(v or ""))
    return [_code(x) for x in items if str(x).strip()]


def _slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(t or "")).strip("-")[:48]


def _nombre(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return v


class Ctx:
    def __init__(self, faits, zones, couches, docs):
        self.faits = faits
        self.zones = zones
        self.couches = couches
        self.docs = docs
        self.q: list[Question] = []
        self._qids: dict[str, Question] = {}
        self.controles: list[dict] = []
        self.idx = defaultdict(list)
        for f in faits:
            self.idx[(f.type, f.cle)].append(f)
        self.reponses = {f.cle: f.valeur for f in faits if f.type == "reponse"}
        self.commentaires = {f.cle: f.valeur for f in faits if f.type == "commentaire"}
        self._commentes: set[str] = set()
        self.corrections = {f.cle: f for f in faits if f.type == "correction"}
        self.renoms = {f.cle.split(".", 1)[1]: f.valeur for f in faits if f.type == "renommage"}
        self.decisions = {"alias_codes": {}, "alias_parcelles": {}, "parcelles_a_ajouter": [],
                          "requalifications_blocs": {}, "couches_contexte": [],
                          "couches_ignorees": [], "decisions_annexes": {},
                          "roles_tableaux": {},
                          "commentaires": []}   # réponses libres du BE -> contexte de la passe 3

    def get(self, type_, cle=None):
        if cle is None:
            fs = [f for f in self.faits if f.type == type_]
        else:
            fs = list(self.idx.get((type_, cle), []))
        users = [f for f in fs if f.methode == "user"]
        return users or fs

    def correction(self, table: str, ligne: str, champ: str):
        return self.corrections.get(f"{table}.{ligne}.{champ}")

    def question(self, regle, portee, texte, options=(), proposition=None, sources=(),
                 cibles=(), bloquante=True):
        """Renvoie la réponse du BE, CORRIGE, ou None (et pose alors la question)."""
        qid = f"{regle}:{portee}"
        if qid in self.reponses:
            rep = self.reponses[qid]
            if rep != LIBRE:
                return rep
            # réponse libre : question close, valeur du système conservée, texte transmis
            if qid not in self._commentes:
                self._commentes.add(qid)
                self.decisions["commentaires"].append({
                    "question": qid, "regle": regle, "portee": portee,
                    "enonce": texte, "texte": self.commentaires.get(qid, ""),
                    "sources": [s.model_dump() for s in sources][:3]})
            return None
        if cibles and all(c in self.corrections for c in cibles):
            return CORRIGE
        if qid in self._qids:                                  # même question, autre témoin
            self._qids[qid].sources += list(sources)
            return None
        q = Question(id=qid, regle=regle, portee=portee, texte=texte,
                     options=[o for o in options if o], proposition=proposition or None,
                     sources=list(sources), bloquante=bloquante)
        self.q.append(q)
        self._qids[qid] = q
        return None

    def controle(self, nom, ok, detail, niveau=None):
        self.controles.append({"controle": nom, "statut": "ok" if ok else (niveau or "alerte"),
                               "detail": detail})


# ================================================================== projet
CONVENTIONS = {
    "Lignes HT, totaux TTC": ("HT", "TTC"),
    "Tout est TTC": ("TTC", "TTC"),
    "Tout est HT (le coefficient est autre chose)": ("HT", "HT"),
}


CHAMPS_ARRETE = {"reference_decision": "decision_reference", "date_decision": "decision_date",
                 "type_procedure": "type_procedure", "maitre_ouvrage": "maitre_ouvrage",
                 "duree_ans": "duree_ans"}


def _meme(champ: str, a, b) -> bool:
    if champ == "reference_decision":
        return re.findall(r"\d+", str(a)) == re.findall(r"\d+", str(b)) and bool(re.findall(r"\d+", str(a)))
    if champ in ("date_decision", "duree_ans"):
        return str(a).strip() == str(b).strip()
    na = norm(re.sub(r"^(la|le|l')\s*", "", str(a), flags=re.I))
    nb = norm(re.sub(r"^(la|le|l')\s*", "", str(b), flags=re.I))
    return na == nb or na in nb or nb in na or recouvrement(str(a), str(b)) >= 0.5


def _concordance(cx: Ctx, P: dict, champ: str, type_: str):
    """Confronte les lectures d'un même champ par des méthodes différentes (regex / LLM)."""
    fs = cx.get(type_, "projet")
    if champ not in P or len({f.methode for f in fs}) < 2 or any(f.methode == "user" for f in fs):
        return
    groupes: list[list] = []
    for f in fs:
        g = next((g for g in groupes if _meme(champ, g[0].valeur, f.valeur)), None)
        (g.append(f) if g else groupes.append([f]))
    c = P[champ]
    if len(groupes) == 1:
        extra = [f.source for f in fs if f.source not in c.sources]
        P[champ] = c.model_copy(update={"confiance": "haute", "sources": c.sources + extra[:3]})
        return
    opts = [str(g[0].valeur) for g in groupes]
    rep = cx.question("arrete_divergence", champ,
                      f"{champ.replace('_', ' ').capitalize()} : les lectures de la décision divergent "
                      f"({' / '.join(opts)}).", opts, str(c.valeur),
                      [g[0].source for g in groupes], cibles=(f"projet.{champ}",))
    if rep not in (None, CORRIGE):
        P[champ] = cellule_be(int(rep) if champ == "duree_ans" and str(rep).isdigit() else rep,
                              [f.source for g in groupes for f in g if str(f.valeur) == str(rep)][:3])
    else:
        P[champ] = c.model_copy(update={"confiance": "basse", "note": "Lectures divergentes de la décision"})


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
            rep = cx.question("ancre_etat_zero", "projet",
                              "Année de l'état zéro : les documents divergent. " + detail,
                              [str(a) for a, _ in classes], str(best),
                              [f.source for _, fs2 in classes for f in fs2[:2]],
                              cibles=("projet.annee_etat_zero",))
            an = _annee(rep) if rep not in (None, CORRIGE) else None
            if an:
                srcs = [f.source for f in votes.get(an, [])][:4]
                P["annee_etat_zero"] = cellule_be(an, srcs)
                P["annee_N"] = cellule_be(an + 1, note="Année N = état zéro + 1")
    if duree:
        srcs = [duree[0].source]
        conf = "moyenne"
        if "annee_fin" in P and "annee_etat_zero" in P and \
                P["annee_fin"].valeur - P["annee_etat_zero"].valeur == duree[0].valeur:
            conf = "haute"
            srcs.append(Source(doc="(calcul)", loc="fin − état zéro",
                               extrait=f"{P['annee_fin'].valeur} − {P['annee_etat_zero'].valeur}"))
        P["duree_ans"] = Cellule(valeur=duree[0].valeur, confiance=conf, sources=srcs)

    # convention des montants : la carte du classeur (en-têtes HT/TTC) prime sur
    # l'heuristique « coefficient constant lignes/totaux ».
    coefs = cx.get("coef_totaux", "projet")
    coef_par_doc = {f.source.doc: f for f in coefs}
    taxes_par_doc = defaultdict(list)
    for f in cx.get("convention_taxe"):
        if f.valeur in ("HT", "TTC"):
            taxes_par_doc[f.source.doc].append(f)
    docs_taxe = set(taxes_par_doc)
    for doc, fs in taxes_par_doc.items():
        votes = Counter(f.valeur for f in fs)
        cle_p = "convention_montants" if len(taxes_par_doc) + len(set(coef_par_doc) - docs_taxe) <= 1 \
            else f"convention_montants · {doc}"
        k = (coef_par_doc[doc].valeur.get("k") if doc in coef_par_doc else None)
        if len(votes) == 1:
            taxe = next(iter(votes))
            P[cle_p] = Cellule(
                valeur={"source": doc, "lignes": taxe, "totaux": taxe, "coefficient": k},
                confiance="haute", sources=[f.source for f in fs[:4]],
                note=f"Lu dans les en-têtes du tableur ({taxe})",
            )
        else:
            opts = [f"Tout est {t}" for t in sorted(votes)]
            rep = cx.question("convention_montants", _slug(doc),
                              f"Les tableaux de {doc} indiquent à la fois HT et TTC. Quelle base retenir ?",
                              opts, opts[0], [f.source for f in fs[:4]],
                              cibles=(f"projet.{cle_p}",))
            taxe = "HT" if rep and "HT" in str(rep) else ("TTC" if rep and "TTC" in str(rep) else None)
            if taxe:
                P[cle_p] = cellule_be({"source": doc, "lignes": taxe, "totaux": taxe, "coefficient": k},
                                      [f.source for f in fs[:3]])
    for f in coefs:
        if f.source.doc in docs_taxe:
            continue
        k, src = f.valeur["k"], f.source
        cle_p = "convention_montants" if len(coefs) == 1 and not docs_taxe else f"convention_montants · {src.doc}"
        rep = cx.question("convention_montants", _slug(src.doc),
                          f"Les totaux de {src.doc} valent les montants × {k} "
                          f"(TVA {round((k - 1) * 100, 1)} % ?). Les montants par ligne sont-ils HT ?",
                          list(CONVENTIONS), "Lignes HT, totaux TTC", [src],
                          cibles=(f"projet.{cle_p}",))
        lignes, totaux = CONVENTIONS.get(rep, (None, None))
        valeur = {"source": src.doc, "lignes": lignes, "totaux": totaux, "coefficient": k}
        if lignes:
            P[cle_p] = cellule_be(valeur, [src])
        else:
            P[cle_p] = Cellule(valeur=valeur, confiance="basse", sources=[src],
                               note=f"{f.valeur['scores'][k]} totaux sur {f.valeur['nb_totaux']} = montant × {k}")

    # deux lectures de l'arrêté (regex + fiche LLM) : concordance = haute, divergence = question
    for champ, type_ in CHAMPS_ARRETE.items():
        _concordance(cx, P, champ, type_)

    # corrections directes du BE sur le bloc projet : priment sur tout ce qui précède
    for cle, f in cx.corrections.items():
        if cle.startswith("projet."):
            P[cle.split(".", 1)[1]] = cellule_be(f.valeur, note="Modifié par vous")
    if "projet.annee_etat_zero" in cx.corrections and "projet.annee_N" not in cx.corrections:
        an = _annee(P["annee_etat_zero"].valeur)
        if an:
            P["annee_N"] = cellule_be(an + 1, note="Recalculé : état zéro + 1")
    return P


# ================================================================== actions
def _actions(cx: Ctx, familles: dict) -> tuple[list[LigneAction], dict]:
    ignore = {f.cle for f in cx.get("action_supprime")}
    ajouts = {f.cle for f in cx.get("action_ajout")}
    codes_plan = {f.cle for f in cx.faits if f.type in ("action_ug", "action_intitule")}
    orphelins = defaultdict(list)
    for f in cx.get("code_tableur"):
        if f.cle not in codes_plan and f.cle not in ignore:
            orphelins[f.cle].append(f)
    for code, fs in sorted(orphelins.items()):
        lib = None
        v0 = fs[0].valeur
        if isinstance(v0, dict):
            lib = v0.get("libelle")
        elif isinstance(v0, str) and v0 != code:
            lib = v0
        extra = f" (« {lib} »)" if lib and str(lib) != code else ""
        rep = cx.question("code_tableur_absent", code,
                          f"Le tableur utilise {code}{extra}, absent du plan de gestion. "
                          "L'ajouter comme action ?",
                          ["Oui, l'ajouter", "Non, ce n'est pas une action"],
                          "Oui, l'ajouter", [f.source for f in fs[:3]])
        if rep == "Oui, l'ajouter":
            ajouts.add(code)
            if lib and not cx.get("action_intitule", code):
                cx.faits.append(fs[0].model_copy(update={
                    "type": "action_intitule", "cle": code, "valeur": lib, "methode": "user"}))
                cx.idx[("action_intitule", code)].append(cx.faits[-1])
    codes = sorted((codes_plan | ajouts) - ignore,
                   key=lambda c: (re.sub(r"\d.*", "", c) or c, int(re.sub(r"\D", "", c) or 0)))
    cells: dict[str, dict[str, Cellule]] = {}
    fusions = defaultdict(list)
    for c in codes:
        # intitulé
        par_doc = {}
        for f in cx.get("action_intitule", c):
            par_doc.setdefault(origine(f), f)
        its = list(par_doc.values())
        if len(its) >= 2 and recouvrement(its[0].valeur, its[1].valeur) >= 0.6:
            it = Cellule(valeur=min((f.valeur for f in its), key=len), confiance="haute",
                         sources=[f.source for f in its])
        elif its:
            it = Cellule(valeur=its[0].valeur, confiance="moyenne", sources=[f.source for f in its],
                         note=None if len(its) == 1 else "Intitulés différents selon les documents")
        else:
            it = Cellule(valeur=None, confiance="basse")
        # nature (famille de codes) — une saisie BE passe par les corrections, plus bas
        pre = re.match(r"[A-Z]+", c)
        fam = familles.get(pre.group(0) if pre else "")
        nat = Cellule(valeur=fam.valeur if fam else None, confiance="haute" if fam else "basse",
                      sources=[fam.source] if fam else [])
        # UG — les listes vides ou faites d'éléments vides ne sont PAS des témoins
        ensembles = {}
        for f in cx.get("action_ug", c):
            val = tuple(sorted(str(u).strip() for u in f.valeur if str(u).strip())) \
                if isinstance(f.valeur, list) else ()
            if val:
                ensembles.setdefault(origine(f), (f, val))
        valeurs = {v for _, v in ensembles.values()}
        douteux = [f for f, _ in ensembles.values() if f.confiance_extraction < 1]
        srcs = [f.source for f, _ in ensembles.values()]
        cible_ug = (f"action.{c}.ugs",)
        if len(ensembles) >= 2 and len(valeurs) == 1 and len(douteux) == len(ensembles):
            v = list(next(iter(valeurs)))
            ug = Cellule(valeur=v, confiance="basse", sources=srcs,
                         note="Cellule fusionnée dans toutes les sources")
            fusions[", ".join(v)].append((c, it.valeur, srcs[0]))
        elif len(ensembles) >= 2 and len(valeurs) == 1:
            ug = Cellule(valeur=list(next(iter(valeurs))), confiance="haute", sources=srcs)
        elif len(valeurs) > 1:
            opts = [", ".join(v) for v in sorted(valeurs)]
            ug = Cellule(valeur=list(min(valeurs, key=len)), confiance="basse",
                         sources=srcs, note="Conflit entre documents")
            rep = cx.question("action_ug_conflit", c,
                              f"{c} : UG différentes selon les documents ({' / '.join(opts)}).",
                              opts, opts[0], srcs, cibles=cible_ug)
            if rep not in (None, CORRIGE):
                ug = cellule_be(_liste(rep), srcs)
        elif ensembles:
            f, v = next(iter(ensembles.values()))
            ug = Cellule(valeur=list(v), confiance="basse" if douteux else "moyenne",
                         sources=[f.source],
                         note="Lu dans une cellule fusionnée" if douteux else "Une seule source")
            if douteux:
                fusions[", ".join(v)].append((c, it.valeur, f.source))
        else:
            ug = Cellule(valeur=[], confiance="basse", note="Aucune UG trouvée")
            rep = cx.question("action_ug_absente", c,
                              f"{c} ({it.valeur}) : aucune UG trouvée. Sur quelles UG porte cette action ?",
                              cibles=cible_ug)
            if rep not in (None, CORRIGE):
                ug = cellule_be(_liste(rep))
        fcib = cx.get("action_cible", c)
        cib = Cellule(valeur=fcib[0].valeur if fcib else None,
                      confiance="moyenne" if fcib else "basse",
                      sources=[fcib[0].source] if fcib else [])
        cells[c] = {"intitule": it, "nature": nat, "ugs": ug, "cible": cib}

    for val, items in fusions.items():
        codes_f = [c for c, _, _ in items]
        rep = cx.question("action_ug_fusion", "+".join(sorted(codes_f)),
                          "Dans le tableur, une même cellule UG fusionnée (" + val + ") couvre plusieurs actions : "
                          + " ; ".join(f"{c} ({i})" for c, i, _ in items)
                          + ". Préciser les UG de chacune (pré-rempli : toutes).",
                          [val], val, [s for _, _, s in items],
                          cibles=tuple(f"action.{c}.ugs" for c in codes_f))
        if rep not in (None, CORRIGE):
            lst = _liste(rep)
            for c in codes_f:
                cells[c]["ugs"] = cellule_be(lst, cells[c]["ugs"].sources,
                                             note="Confirmé par vous" if ", ".join(lst) == val else "Modifié par vous")

    lignes, ug_par_code = [], {}
    for c in codes:
        for champ in ("intitule", "nature", "ugs", "cible"):
            f = cx.correction("action", c, champ)
            if f is not None:
                cells[c][champ] = cellule_be(_liste(f.valeur) if champ == "ugs" else f.valeur,
                                             note="Modifié par vous")
        ug_par_code[c] = cells[c]["ugs"].valeur or []
        lignes.append(LigneAction(code=c, **cells[c]))
    return lignes, ug_par_code


def _blocs_incoherents(cx: Ctx, familles: dict, ug_par_code: dict, parc_ug: dict) -> set:
    """Bloc dont le titre évoque une famille (ex. « entretien ») mais codé avec une autre.
    Une seule question par titre de bloc (versions planning / coûts du même tableau)."""
    groupes = {}
    for f in cx.get("bloc_codes"):
        titre = norm(f.valeur["titre"])
        fam_codes = {m.group(0) for c in f.valeur["codes"] if (m := re.match(r"[A-Z]+", c))}
        for pre, ff in familles.items():
            autres = {w for p2, f2 in familles.items() if p2 != pre
                      for w in re.findall(r"[a-z]{6,}", norm(f2.valeur))}
            mots = [w for w in re.findall(r"[a-z]{6,}", norm(ff.valeur)) if w not in autres]
            if not any(w[:7] in titre for w in mots) or pre in fam_codes:
                continue
            cle = (_slug(re.sub(r"\(.*?\)", "", f.valeur["titre"])), pre)
            g = groupes.setdefault(cle, {"famille": ff, "titre": f.valeur["titre"], "blocs": [],
                                         "codes": set(), "ugs": set(), "sources": []})
            g["blocs"].append(f.cle)
            g["codes"] |= set(f.valeur["codes"])
            g["sources"].append(f.source)
            for p in f.valeur["parcelles"]:
                g["ugs"] |= parc_ug.get(p, set())

    suspects = set()
    requal = cx.decisions["requalifications_blocs"]
    for (slug, pre), g in groupes.items():
        ugs = g["ugs"]
        cands = sorted([c for c, u in ug_par_code.items() if c.startswith(pre) and ugs and set(u) >= ugs],
                       key=lambda c: len(ug_par_code[c]))
        prop = cands[0] if cands else None
        garder = "Garder " + ", ".join(sorted(g["codes"]))
        docs = ", ".join(sorted({s.doc for s in g["sources"]}))
        rep = cx.question("bloc_code_incoherent", f"{slug}:{pre}",
                          f"Le bloc « {g['titre']} » ({docs}) est codé {', '.join(sorted(g['codes']))} "
                          f"alors que son titre correspond à la famille {pre} ({g['famille'].valeur}). "
                          f"Ses parcelles relèvent de {', '.join(sorted(ugs)) or '?'}."
                          + (f" S'agit-il de {prop} ?" if prop else ""),
                          ([prop] if prop else []) + [garder], prop, g["sources"])
        if rep is None:
            suspects |= set(g["blocs"])
        elif rep != garder:
            for b in g["blocs"]:
                requal[b] = _code(rep)
    return suspects


# ================================================================== UG ↔ zones
def _corriger_ug(cx: Ctx, u: str, libelle: Cellule, typ: Cellule, surf: Cellule):
    f = cx.correction("ug", u, "libelle")
    if f is not None:
        libelle = cellule_be(f.valeur, note="Modifié par vous")
    f = cx.correction("ug", u, "type_erc")
    if f is not None:
        typ = cellule_be(f.valeur, note="Modifié par vous")
    f = cx.correction("ug", u, "surface_ha")
    if f is not None:
        surf = cellule_be(_nombre(f.valeur), note="Modifié par vous")
    return libelle, typ, surf


def _ugs(cx: Ctx, ug_par_code: dict, actions: list[LigneAction], suspects: set):
    suppr = {f.cle for f in cx.get("ug_supprime")}
    ug_codes = sorted({u for us in ug_par_code.values() for u in us} |
                      {f.cle for f in cx.get("ug_libelle")} |
                      {f.cle for f in cx.get("ug_ajout")} |
                      {z.ug_attribut for z in cx.zones if z.ug_attribut},
                      key=lambda u: int(re.sub(r"\D", "", u) or 0))
    ug_codes = [u for u in ug_codes if u and u not in suppr]
    lib = {u: cx.get("ug_libelle", u) for u in ug_codes}
    cibles_ug = defaultdict(str)
    for a in actions:
        for u in a.ugs.valeur or []:
            cibles_ug[u] += " " + (a.cible.valeur or "") + " " + (a.intitule.valeur or "")

    # parcelles et surfaces attendues par UG (codes mono-UG, blocs cohérents ou requalifiés)
    requal = cx.decisions["requalifications_blocs"]
    attendu_refs, attendu_surf = defaultdict(set), defaultdict(dict)
    multi_refs = defaultdict(set)
    for f in cx.get("code_parcelle"):
        bloc = f.valeur["bloc"]
        if bloc in suspects:
            continue
        us = ug_par_code.get(requal.get(bloc, f.cle), [])
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
        l = norm(lib[u][0].valeur) if lib.get(u) else ""
        return "E" if "evit" in l else None

    def ref_overlap(zrefs, refs):
        if not zrefs or not refs:
            return 0.0
        return sum(any(correspond(z, r) for r in refs) for z in zrefs) / len(zrefs)

    zones_par_id = {z.id: z for z in cx.zones}
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

    def zsource(z):
        return Source(doc="SIG", loc=z.id, extrait=f"{z.nb_entites} entité(s), {z.surface_ha} ha")

    for u in ug_codes:
        l = lib[u]
        libelle = Cellule(valeur=l[0].valeur if l else None, confiance="moyenne" if l else "basse",
                          sources=[f.source for f in l])
        tl = type_label(u)
        cible_z = (f"ug.{u}.zone_sig",)
        # --- géométrie
        if u in assign:
            z, mode, pr = assign[u]
            zs = zsource(z)
            if mode == "attribut":
                zone = Cellule(valeur=z.id, confiance="haute" if pr >= 0.5 else "moyenne", sources=[zs],
                               note=f"Attribut « {list(z.filtre)[0]} » ; {int(pr * 100)} % des parcelles "
                                    "retrouvées dans les documents")
            else:
                zone = Cellule(valeur=z.id, confiance="moyenne", sources=[zs],
                               note="Déduit : " + ", ".join(f"{k} {v}" for k, v in pr.items() if v))
                rep = cx.question("ug_zone_inferee", u,
                                  f"{u} ({libelle.valeur}) : la couche « {z.couche} » n'a pas d'attribut UG. "
                                  f"Rapprochement proposé d'après {', '.join(k for k, v in pr.items() if v and v > 0)}. "
                                  "Confirmer ?", ["Oui", "Autre couche", "Pas de géométrie"], "Oui", [zs],
                                  cibles=cible_z)
                if rep == "Oui":
                    zone = cellule_be(z.id, [zs], note="Rapprochement confirmé par vous")
                elif rep == "Pas de géométrie":
                    zone = cellule_be(None, note="Sans géométrie (confirmé)")
                    assign.pop(u)
                elif rep in zones_par_id:
                    zone = cellule_be(rep, [zsource(zones_par_id[rep])])
                    assign[u] = (zones_par_id[rep], "user", {})
                elif rep not in (None, CORRIGE):              # « Autre couche » sans la préciser
                    zone = Cellule(valeur=None, confiance="basse",
                                   note="Autre couche demandée : la choisir dans la colonne Géométrie")
                    assign.pop(u)
                    cx.controle(f"Géométrie {u}", False,
                                "Autre couche demandée : saisir l'identifiant de zone dans la colonne Géométrie")
        else:
            zone = Cellule(valeur=None, confiance="basse", note="Aucune géométrie rapprochée")
            rep = cx.question("ug_sans_zone", u,
                              f"{u} ({libelle.valeur}) : aucune géométrie trouvée dans le SIG. "
                              "Quelle couche/entité la représente ?",
                              [z.id for z in libres] + ["Pas de géométrie"], None, cibles=cible_z)
            if rep in zones_par_id:
                zone = cellule_be(rep, [zsource(zones_par_id[rep])])
                assign[u] = (zones_par_id[rep], "user", {})
            elif rep == "Pas de géométrie":
                zone = cellule_be(None, note="Sans géométrie (confirmé)")
        fz = cx.correction("ug", u, "zone_sig")
        if fz is not None:
            v = fz.valeur or None
            zone = cellule_be(v, note="Modifié par vous")
            if v in zones_par_id:
                assign[u] = (zones_par_id[v], "user", {})
            else:
                assign.pop(u, None)
                if v:
                    cx.controle(f"Géométrie {u}", False, f"Zone « {v} » inconnue du SIG déposé")
        # --- type et surface, depuis la géométrie retenue
        z = assign[u][0] if u in assign else None
        if z:
            zs = zsource(z)
            typ = Cellule(valeur=z.type_erc_nom or tl,
                          confiance="haute" if (tl or "C") == z.type_erc_nom else "moyenne",
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
            typ = Cellule(valeur=tl, confiance="moyenne" if tl else "basse", sources=[f.source for f in l])
            surf = Cellule(valeur=None, confiance="basse")
        libelle, typ, surf = _corriger_ug(cx, u, libelle, typ, surf)
        lignes.append(LigneUG(ug_code=u, libelle=libelle, type_erc=typ, zone_sig=zone, surface_ha=surf))

    # --- couches sans UG
    mesures = cx.get("mesure_plan")
    contexte = cx.decisions["couches_contexte"]
    for z in cx.zones:
        if any(v[0].id == z.id for v in assign.values()):
            continue
        nz = stems(z.couche)
        m = max(mesures, key=lambda f: len(nz & (stems(f.valeur.get("cible") or "") | stems(f.valeur["libelle"]))),
                default=None)
        if m and m.valeur.get("non_soumis") and m.valeur["type_erc"] == (z.type_erc_nom or "") \
                and nz & stems(m.valeur.get("cible") or ""):
            cx.controle(f"Couche {z.couche}", True,
                        f"= mesure {m.cle} ({m.valeur['cible']}) : non soumise à obligation de résultats, "
                        f"hors plan de gestion ({m.source.doc} {m.source.loc}) → couche de contexte", "info")
            contexte.append({"zone": z.id, "motif": f"mesure {m.cle} non soumise à obligation de résultats"})
            continue
        couche = next((c for c in cx.couches if c.nom == z.couche), None)
        extra = f" (reprojetée depuis EPSG:{couche.epsg})" if couche and couche.reprojetee else ""
        zs = Source(doc="SIG", loc=z.id)
        rep = cx.question("couche_orpheline", z.id,
                          f"La couche « {z.couche} »{extra}, {z.surface_ha} ha, type {z.type_erc_nom or '?'}, "
                          "ne correspond à aucune UG du plan de gestion. Que représente-t-elle ?",
                          ["Évitement sans gestion (contexte)", "Nouvelle UG", "Ignorer"],
                          "Évitement sans gestion (contexte)" if z.type_erc_nom == "E" else None, [zs])
        if rep == "Évitement sans gestion (contexte)":
            contexte.append({"zone": z.id, "motif": "confirmé par le BE"})
        elif rep == "Ignorer":
            cx.decisions["couches_ignorees"].append(z.id)
        elif rep == "Nouvelle UG":
            code = cx.renoms.get(z.couche, z.couche)          # renommable au tour suivant
            if code in suppr or any(x.ug_code == code for x in lignes):
                continue
            libelle, typ, surf = _corriger_ug(
                cx, code, cellule_be(z.couche, note="Créée depuis la couche SIG"),
                Cellule(valeur=z.type_erc_nom, confiance="moyenne", sources=[zsource(z)]),
                Cellule(valeur=z.surface_ha, confiance="haute", sources=[zsource(z)]))
            lignes.append(LigneUG(ug_code=code, libelle=libelle, type_erc=typ,
                                  zone_sig=cellule_be(z.id, [zsource(z)]), surface_ha=surf))
            assign[code] = (z, "user", {})
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
            rep = cx.question("parcelle_introuvable", r["cle"],
                              f"Parcelle « {r['brut']} » citée dans la fiche {f.cle} ({f.source.loc}) "
                              f"introuvable dans le SIG et le tableur."
                              + (f" Les documents contiennent {', '.join(voisins)} : même parcelle ?" if voisins else ""),
                              voisins + ["Parcelle à ajouter"], voisins[0] if voisins else None, [f.source])
            if rep == "Parcelle à ajouter":
                cx.decisions["parcelles_a_ajouter"].append(r["cle"])
            elif rep not in (None, CORRIGE):
                cx.decisions["alias_parcelles"][r["cle"]] = str(rep).strip()
                cx.controle("Référence cadastrale", True, f"« {r['brut']} » = {rep} (validé par vous)", "info")


def _obligations(cx: Ctx, actions, ugs_lignes, assign):
    out = []
    type_ug = {l.ug_code: l.type_erc.valeur for l in ugs_lignes}
    especes = set()
    for a in actions:
        for e in re.split(r"\bet\b|,", a.cible.valeur or ""):
            if len(e.strip()) > 6:
                especes.add(e.strip())
    vues = set()
    for f in cx.get("obligation_surface"):
        empreinte = (round(f.valeur["surface_min_ha"], 2), f.valeur.get("nb_parcelles"))
        if empreinte in vues or (round(f.valeur["surface_min_ha"], 2), None) in vues:
            continue                                   # même obligation lue par regex et par LLM
        vues.add(empreinte)
        txt = f.valeur["texte"]
        esp = [e for e in especes if norm(e) in norm(txt)]
        if esp:
            codes = [a for a in actions if any(norm(e) in norm(a.cible.valeur or "") for e in esp)]
            base = f"espèce : {', '.join(esp)}"
        else:
            sc = [(len(stems(txt) & stems(a.intitule.valeur or "")), a) for a in actions]
            mx = max((s for s, _ in sc), default=0)
            codes = [a for s, a in sc if s == mx and mx >= 2]
            base = f"intitulé : {', '.join(a.code for a in codes)}"
        ugs = sorted({u for a in codes for u in (a.ugs.valeur or []) if type_ug.get(u) == "C"})
        zs = [assign[u][0] for u in ugs if u in assign]
        surf = round(sum(z.surface_ha for z in zs), 2)
        nb = sum(z.nb_entites for z in zs)
        ok_s = surf >= f.valeur["surface_min_ha"] * 0.98
        ok_n = f.valeur.get("nb_parcelles") is None or nb == f.valeur["nb_parcelles"]
        out.append({"mesure": f.cle, "surface_min_ha": f.valeur["surface_min_ha"],
                    "nb_parcelles": f.valeur.get("nb_parcelles"), "ugs": ugs, "rattachement": base,
                    "surface_sig_ha": surf, "nb_entites_sig": nb, "source": f.source.model_dump()})
        cx.controle(f"Obligation {f.cle}", ok_s and ok_n,
                    f"{f.valeur['surface_min_ha']} ha min / {f.valeur.get('nb_parcelles') or '?'} parcelles (arrêté) — "
                    f"SIG {', '.join(ugs)} : {surf} ha / {nb} entités"
                    + ("" if ok_s else " — surface insuffisante") + ("" if ok_n else " — nb de parcelles différent")
                    + (" (tolérance 2 % appliquée)" if ok_s and surf < f.valeur["surface_min_ha"] else ""))
    evit_vus = set()
    for f in cx.get("evitement_surface"):
        for e in f.valeur:
            if round(e["surface_ha"], 1) in evit_vus:
                continue
            evit_vus.add(round(e["surface_ha"], 1))
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
        t = next((k for k in tab if esp and esp in norm(k)), None) if v["type_erc"] == "C" else None
        somme = round(sum(tab[t].values()), 2) if t else None
        tol = max(0.03 * v["surface_ha"], 0.05)
        ecarts = [x for x in (sig or None, somme) if x and abs(x - v["surface_ha"]) > tol]
        detail = (f"{v['surface_ha']} ha annoncés ({f.source.loc})"
                  + (f" — tableau de parcelles {somme} ha" if somme else "")
                  + (f" — SIG {', '.join(ugs)} {sig} ha" if ugs else ""))
        cx.controle(f"Mesure {f.cle} du plan de gestion", not ecarts,
                    detail + (" — incohérence interne au dossier, à signaler au BE" if ecarts else ""))


ANNEXE = {"Oui, l'extraire de l'annexe": "extraire", "Je le dépose séparément": "attendu",
          "Non pertinent": "ignore"}


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
                rep = cx.question("annexe_decision", f"{_slug(d.nom)}:{cle}",
                                  f"L'annexe {a['num']} du plan de gestion contient une autre décision"
                                  + (f" ({resume})" if resume else f" (« {a['titre'][:70]} »)")
                                  + (", en pages scannées" if a.get("pages_scannees") and not resume else "")
                                  + ", non fournie séparément. L'ajouter comme décision du projet ?",
                                  list(ANNEXE), "Oui, l'extraire de l'annexe")
                if rep not in (None, CORRIGE):
                    cx.decisions["decisions_annexes"][cle] = {
                        "document": d.nom, "pages": [a.get("page_debut"), a.get("page_fin")],
                        "statut": ANNEXE.get(rep, rep), "faits": info}
        for w in d.avertissements:
            cx.controle("Document", False, f"{d.nom} : {w}", "info")
    for c in cx.couches:
        for w in c.avertissements:
            cx.controle(f"SIG {c.nom}", True, w, "info")
    for f in cx.get("legende_conflit"):
        codes = list(f.valeur["codes"])
        rep = cx.question("legende_conflit", "+".join(sorted(codes)),
                          f"Le tableur utilise deux abréviations pour « {f.valeur['libelle']} » : "
                          f"{' et '.join(codes)}. Même opération ?",
                          ["Oui, même opération", "Non, opérations différentes"], "Oui, même opération", [f.source])
        if rep == "Oui, même opération":
            for c in codes[1:]:
                cx.decisions["alias_codes"][c] = codes[0]


ROLES_TABLEAU = ("planning_passages", "planning_montants", "couts_unitaires", "decompte_realise",
                 "statuts", "synthese", "parcelles", "autre")


def _tableaux(cx: Ctx):
    """Rôles de tableaux : faits LLM + corrections BE → decisions.roles_tableaux."""
    roles = cx.decisions["roles_tableaux"]
    for f in cx.get("tableau_role"):
        val = f.valeur.get("role") if isinstance(f.valeur, dict) else f.valeur
        if val in ROLES_TABLEAU:
            roles[f.cle] = val
    for cle, f in cx.corrections.items():
        if not (cle.startswith("tableau.") and cle.endswith(".role")):
            continue
        tid = cle.split(".")[1]
        if f.valeur in ROLES_TABLEAU:
            roles[tid] = f.valeur


# ================================================================== point d'entrée
def reconcilier(faits, zones, couches, docs) -> ReferentielPropose:
    faits, zones = appliquer_renommages(faits, zones)
    cx = Ctx(faits, zones, couches, docs)
    familles = {}
    for f in cx.get("famille_code"):
        familles.setdefault(f.cle, f)
    projet = _projet(cx)
    actions, ug_par_code = _actions(cx, familles)
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
    _tableaux(cx)
    cx.decisions["arrete"] = {f.cle: f.valeur for f in cx.faits if f.type == "fiche_arrete"}

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
             "questions_bloquantes": sum(q.bloquante for q in cx.q),
             "faits": len(faits),
             "faits_user": sum(f.methode == "user" for f in faits),
             "geomce_pret_pct": round(100 * remplis / max(1, total))}
    return ReferentielPropose(projet=projet, ugs=ugs, actions=actions,
                              obligations_surfaciques=obligations, questions=cx.q,
                              controles=cx.controles, decisions=cx.decisions, stats=stats)