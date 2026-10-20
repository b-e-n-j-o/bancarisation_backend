"""Étape 6 — Exports : `arrete.json` (format complet, cf. schemas.py) et `rapport.md`
(même ordre que la future page « Arrêté » : fiche, synthèse, articles, obligations,
échéancier, figures, contrôles)."""
from __future__ import annotations

from pathlib import Path

from .schemas import ArreteAnalyse

DEC = {"retenue": "retenue", "a_valider": "à valider", "a_revoir": "à revoir"}
TYPE_M = {"E": "Évitement", "R": "Réduction", "C": "Compensation", "A": "Accompagnement", "S": "Suivi"}


def _q(qs) -> str:
    out = []
    for q in qs:
        v = "?" if q.valeur is None else f"{q.valeur:g}"
        if q.valeur_max is not None:
            v += f" à {q.valeur_max:g}"
        out.append(f"{v} {q.unite} {q.objet}".strip())
    return " ; ".join(out)


def write_report(res: ArreteAnalyse, path: Path) -> None:
    f, s = res.fiche, res.synthese
    L = [f"# {f.titre or 'Arrêté'}", "", f"**{f.reference}** · {f.type_acte} · signé le {f.date_signature or '?'} "
         f"à {f.lieu_signature or '?'} · {f.autorite}", ""]

    # 1. fiche
    L += ["## 1. Fiche d'identité", "", f"- Procédure : {f.procedure or '—'}",
          f"- Projet : {f.projet.nom} — {f.projet.nature} ({', '.join(f.projet.communes)} {f.projet.departement})"]
    for p in f.parties:
        L.append(f"- {p.role} : {p.nom}" + (f" ({p.qualite})" if p.qualite else "") + (f" — {p.adresse}" if p.adresse else "")
                 + (f" — {p.identifiant}" if p.identifiant else "") + (f" — {p.contact}" if p.contact else ""))
    if f.visas:
        L += ["", "**Historique (VU)**", "", "| Date | Type | Visa |", "|---|---|---|"]
        L += [f"| {v.date or '—'}{' → ' + v.date_fin if v.date_fin else ''} | {v.type} | {v.libelle} |" for v in f.visas]
    if f.bases_juridiques:
        L += ["", "**Bases juridiques** : " + " ; ".join(f.bases_juridiques)]
    if f.actes_lies:
        L += ["", "**Actes liés** : " + " ; ".join(f"{a.relation} {a.reference} ({a.date or '?'})" for a in f.actes_lies)]
    L += ["", f"**Recours** : {f.recours.delai} — {f.recours.juridiction} — {' ; '.join(f.recours.modalites)}", ""]

    # 2. synthèse
    L += ["## 2. Synthèse", "", s.resume_general, ""]
    if s.justification.resume:
        L += [f"**Justification** : {s.justification.resume}"] + [f"- {m}" for m in s.justification.motifs + s.justification.chiffres_cles] + [""]
    L += [f"**Espèces : {s.nb_especes}** — par groupe {s.especes_par_groupe} — par nature de dérogation {s.especes_par_nature}", ""]
    if s.especes:
        L += ["| Espèce | Groupe | Natures |", "|---|---|---|"]
        L += [f"| {e.nom_vernaculaire} (*{e.nom_scientifique}*) | {e.groupe} | {', '.join(e.natures)} |" for e in s.especes]
        L.append("")
    if s.impacts:
        L += ["**Impacts autorisés (plafonds)**"] + [f"- {i.description}" + (f" — {_q(i.quantites)}" if i.quantites else "") for i in s.impacts] + [""]
    L += [f"**Périmètre** : {s.perimetre}", ""]
    if s.durees:
        L += ["**Durées**"] + [f"- {d.objet} : " + (f"{'≥ ' if d.minimale else ''}{d.valeur:g} {d.unite}" if d.valeur else "")
                              + (f" jusqu'au {d.date_fin}" if d.date_fin else "") + (f" (depuis {d.debut})" if d.debut else "") for d in s.durees] + [""]
    if s.sites:
        L += ["**Sites**"] + [f"- {x.type} — {x.nom}" + (f", {x.surface_m2:g} m²" if x.surface_m2 else "")
                             + (f", opérateur {x.operateur}" if x.operateur else "") + (f" — {x.description}" if x.description else "") for x in s.sites] + [""]
    L += [f"**Mesures par type** : {s.mesures_par_type}", f"**Gouvernance** : {s.gouvernance}", ""]
    if s.points_cles:
        L += ["**À retenir**"] + [f"- {p}" for p in s.points_cles] + [""]

    # 3. articles
    L += ["## 3. Articles", ""]
    obl_by_art: dict[str, list] = {}
    for o in res.obligations:
        for a in [o.article] + o.articles_lies:
            obl_by_art.setdefault(a, []).append(o)
    for a in res.articles:
        L.append(f"{'#' * min(6, 2 + a.niveau)} Art. {a.numero} — {a.titre}  _(p.{a.page_debut}{'-' + str(a.page_fin) if a.page_fin != a.page_debut else ''})_")
        if a.categories:
            L.append(f"`{' · '.join(a.categories)}`")
        if a.resume:
            L.append(a.resume)
        for m in a.mesures:
            L.append(f"- **{TYPE_M[m.type]} {m.code}** — {m.intitule}" + (f" ({m.localisation})" if m.localisation != "non_precise" else "")
                     + (f" — {_q(m.quantites)}" if m.quantites else "") + (f" — réf. {', '.join(m.references_dossier)}" if m.references_dossier else ""))
        for o in obl_by_art.get(a.numero, []):
            L.append(f"  - ☐ {o.id} {o.intitule} — _{o.echeance.texte or o.echeance.kind}_ — {DEC[o.decision]} {o.confiance.score:.2f}")
        L.append("")

    # 4. obligations
    L += ["## 4. Obligations", "", "| Id | Art. | Catégorie | Obligation | Échéance | Preuve | Décision |", "|---|---|---|---|---|---|---|"]
    for o in res.obligations:
        arts = o.article + (" + " + ", ".join(o.articles_lies) if o.articles_lies else "")
        L.append(f"| {o.id} | {arts} | {o.categorie} | {o.intitule}{' — si ' + o.condition if o.condition else ''} | "
                 f"{o.echeance.texte or o.echeance.kind} | {', '.join(o.preuve_attendue)} | {DEC[o.decision]} {o.confiance.score:.2f} |")
    L.append("")

    # 5. échéancier
    L += ["## 5. Échéancier", "", f"Ancres : {res.ancres}", "",
          "| Date | Échéance | Obligation | Art. | Preuve attendue | Statut |", "|---|---|---|---|---|---|"]
    for r in res.echeancier:
        d = ", ".join(r.dates) if r.dates else (r.date or "—")
        L.append(f"| {d} | {r.libelle}{' (' + r.hypothese + ')' if r.hypothese else ''} | {r.intitule} | {r.article} | "
                 f"{', '.join(r.preuve_attendue)} | {r.statut} |")
    L.append("")

    # 6. figures, 7. contrôles
    if res.figures:
        L += ["## 6. Figures", ""] + [f"- Fig. {x.numero} (p.{x.page}, art. {x.article or '—'}) — {x.legende}"
                                      + (f" — {x.contenu}" if x.contenu else "") + (f" — valeurs : {', '.join(x.valeurs)}" if x.valeurs else "")
                                      for x in res.figures] + [""]
    L += ["## 7. Contrôles", ""]
    for c in sorted(res.controles, key=lambda c: c.gravite != "alerte"):
        L.append(f"- **[{c.gravite}] {c.sujet}** ({c.origine}{', art. ' + ', '.join(c.articles) if c.articles else ''}) — {c.description}")
        for ci in c.citations[:2]:
            L.append(f"  > p.{ci.page} « {ci.quote[:200]} »")
    path.write_text("\n".join(L), encoding="utf-8")
