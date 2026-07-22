"""Rendu PDF du bilan financier annuel.

Emplacement : backend/api/budget/rapport/pdf.py
Template     : backend/api/budget/rapport/templates/bilan_annuel.html.j2

Dépendances : weasyprint, jinja2
    pip install weasyprint jinja2
(WeasyPrint requiert les libs système pango/cairo — sur Debian/Ubuntu :
 apt-get install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi-dev)

Principe : le PDF est un RENDU du snapshot `donnees` archivé, jamais un
recalcul. Régénérer le PDF d'un bilan validé redonne exactement le même
document, puisque la source est figée en base.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "bilan_annuel.html.j2"

# Libellés d'affichage --------------------------------------------------

STATUT_LABELS = {
    "a_confirmer": "À confirmer",
    "planifie": "Planifiée",
    "en_cours": "En cours",
    "realise": "Réalisée",
    "repousse": "Reportée",
    "supprime": "Supprimée",
}

CHAMP_LABELS = {
    "montant_ht": "Prévu",
    "montant_engage": "Engagé",
    "montant_realise": "Réalisé",
    "statut": "Statut",
    "annee": "Exercice",
}

INDIC_LABELS = {
    "initial": "Budget initial",
    "prevu": "Prévu actualisé",
    "engage": "Engagé",
    "realise": "Réalisé",
}

CATEGORIE_LABELS = {
    "MG": "Mesures de gestion",
    "SE": "Suivis écologiques",
    "TU": "Travaux et aménagements",
    "AD": "Administration et coordination",
}

STATUTS_SOLDES = {"realise", "repousse", "supprime"}


# Filtres ---------------------------------------------------------------

def f_eur(v: Any, signe: bool = False, court: bool = False) -> str:
    """Montant formaté à la française : 12 345 € (espaces insécables fines)."""
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if court and abs(n) >= 10000:
        corps = f"{n / 1000:,.1f}".replace(",", "\u202f").replace(".", ",")
        corps = corps.rstrip("0").rstrip(",")
        txt = f"{corps} k€"
    else:
        txt = f"{n:,.0f}".replace(",", "\u202f") + "\u202f€"
    if signe and n > 0:
        txt = "+" + txt
    return txt


def f_eur_ou_tiret(v: Any) -> str:
    return "—" if v is None else f_eur(v)


def f_pct(v: Any) -> str:
    if v is None:
        return "—"
    return f"{float(v) * 100:.1f}\u202f%".replace(".", ",")


def f_dt(v: Any, heure: bool = True) -> str:
    if not v:
        return "—"
    try:
        s = str(v).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
    except ValueError:
        return str(v)
    return d.strftime("%d/%m/%Y à %Hh%M") if heure else d.strftime("%d/%m/%Y")


def f_statut_label(v: Any) -> str:
    return STATUT_LABELS.get(str(v), str(v))


def f_champ_label(v: Any) -> str:
    return CHAMP_LABELS.get(str(v), str(v))


def f_indic_label(v: Any) -> str:
    return INDIC_LABELS.get(str(v), str(v))


def f_val_affichee(v: Any, champ: str = "") -> str:
    """Valeur du journal : montant formaté, statut traduit, reste tel quel."""
    if v is None or v == "":
        return "—"
    if champ in ("montant_ht", "montant_engage", "montant_realise"):
        return f_eur(v)
    if champ == "statut":
        return f_statut_label(v)
    return str(v)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters.update(
        eur=f_eur,
        eur_ou_tiret=f_eur_ou_tiret,
        pct=f_pct,
        dt=f_dt,
        statut_label=f_statut_label,
        champ_label=f_champ_label,
        indic_label=f_indic_label,
        val_affichee=f_val_affichee,
        abs=abs,
    )
    return env


# Préparation du contexte ------------------------------------------------

def _phrase_synthese(d: dict, s: dict, ecarts: dict) -> str:
    annee = d.get("annee")
    parts = []
    if s.get("taux_execution") is not None:
        parts.append(
            f"En {annee}, {f_pct(s['taux_execution'])} du budget prévu a été facturé"
        )
    else:
        parts.append(f"En {annee}, aucun budget prévu n'est renseigné")
    if s.get("taux_engagement") is not None:
        parts.append(f"{f_pct(s['taux_engagement'])} a été contractualisé")
    phrase = ", ".join(parts) + "."

    suites = []
    if (ecarts.get("glisse_sortant") or 0) >= 1:
        suites.append(
            f"{f_eur(ecarts['glisse_sortant'])} ont été reportés sur un autre exercice"
        )
    if (ecarts.get("annule") or 0) >= 1:
        suites.append(
            f"{f_eur(ecarts['annule'])} ont été économisés sur des actions annulées"
        )
    if (ecarts.get("ajoute") or 0) >= 1:
        suites.append(
            f"{f_eur(ecarts['ajoute'])} correspondent à des actions ajoutées "
            "après le figeage du budget de référence"
        )
    if suites:
        phrase += " Sur l'exercice, " + ", ".join(suites) + "."
    return phrase


def _grouper_par_categorie(lignes: list[dict]) -> list[tuple[str, dict]]:
    groupes: dict[str, list[dict]] = {}
    for l in lignes:
        cat = str(l.get("categorie") or "AUTRE")
        groupes.setdefault(cat, []).append(l)

    out = []
    for cat in sorted(groupes):
        items = sorted(groupes[cat], key=lambda x: (str(x.get("code") or ""), str(x.get("titre") or "")))
        totaux = {
            k: sum(float(i.get(src) or 0) for i in items)
            for k, src in (
                ("initial", "montant_initial"),
                ("prevu", "prevu"),
                ("engage", "engage"),
                ("realise", "realise"),
            )
        }
        out.append((CATEGORIE_LABELS.get(cat, cat), {"lignes": items, "totaux": totaux}))
    return out


def _waterfall(s: dict, ecarts: dict) -> tuple[dict, float]:
    """Postes de passage initial -> prévu, avec largeurs de barres."""
    postes = [
        {
            "libelle": "Reports vers d'autres exercices",
            "aide": "actions déplacées hors de l'exercice de référence",
            "montant": -abs(float(ecarts.get("glisse_sortant") or 0)),
        },
        {
            "libelle": "Actions annulées",
            "aide": "économies constatées sur des actions supprimées",
            "montant": -abs(float(ecarts.get("annule") or 0)),
        },
        {
            "libelle": "Actions ajoutées",
            "aide": "actions apparues après le figeage du budget de référence",
            "montant": float(ecarts.get("ajoute") or 0),
        },
        {
            "libelle": "Révisions de prix",
            "aide": "montants réévalués sur des actions maintenues",
            "montant": float(ecarts.get("revision_prix") or 0),
        },
    ]
    postes = [p for p in postes if abs(p["montant"]) >= 1]

    initial = float(s.get("initial") or 0)
    prevu = float(s.get("prevu") or 0)
    ref = max(initial, prevu, 1.0)
    maxi = max([abs(p["montant"]) for p in postes] + [1.0])

    for p in postes:
        p["width"] = round(min(abs(p["montant"]) / maxi * 100, 100), 1)

    explique = sum(p["montant"] for p in postes)
    residu = (prevu - initial) - explique

    return (
        {
            "postes": postes,
            "ref_width": round(initial / ref * 100, 1),
            "fin_width": round(prevu / ref * 100, 1),
        },
        residu,
    )


def preparer_contexte(bilan: dict[str, Any]) -> dict[str, Any]:
    """Transforme la ligne rapport_bilan (ou un snapshot nu) en contexte template."""
    d = bilan.get("donnees") or bilan
    meta = {
        "id": bilan.get("id"),
        "version": bilan.get("version", 1),
        "statut": bilan.get("statut", "brouillon"),
        "valide_le": bilan.get("valide_le"),
        "valide_par": bilan.get("valide_par"),
    }

    s = dict(d.get("synthese") or {})
    for k in ("initial", "prevu", "engage", "realise", "delta_prevu_initial"):
        s[k] = float(s.get(k) or 0)

    lignes = list(d.get("lignes") or [])
    ecarts = dict(d.get("ecarts") or {})
    controles = list(d.get("controles") or bilan.get("controles") or [])

    pluri = dict(d.get("pluriannuel") or {})
    annees = list(pluri.get("annees") or [])
    maxi_prevu = max([float(a.get("prevu") or 0) for a in annees] + [1.0])
    for a in annees:
        a["width"] = round(float(a.get("prevu") or 0) / maxi_prevu * 100, 1)
    pluri["annees"] = annees
    pluri["totaux"] = {
        k: sum(float(a.get(k) or 0) for a in annees)
        for k in ("initial", "prevu", "engage", "realise")
    }
    pluri.setdefault("cumul_realise_fin_annee", 0.0)
    pluri.setdefault("restant_a_engager_apres_annee", 0.0)

    totaux = {
        k: sum(float(l.get(src) or 0) for l in lignes)
        for k, src in (
            ("initial", "montant_initial"),
            ("prevu", "prevu"),
            ("engage", "engage"),
            ("realise", "realise"),
        )
    }
    totaux["ecart"] = totaux["prevu"] - totaux["initial"]

    non_justifiees = [
        l for l in lignes
        if l.get("ecart_initial") not in (None, 0)
        and abs(float(l["ecart_initial"])) >= 1
        and not (l.get("motifs") or {})
    ]

    soldees = [l for l in lignes if l.get("statut") in STATUTS_SOLDES]
    taux = s.get("taux_execution")
    sous_execution = (
        taux is not None
        and lignes
        and len(soldees) / len(lignes) >= 0.7
        and taux < 0.7
    )

    wf, residu = _waterfall(s, ecarts)

    return {
        "d": d,
        "meta": meta,
        "s": s,
        "lignes": lignes,
        "lignes_par_categorie": _grouper_par_categorie(lignes),
        "lignes_non_justifiees": non_justifiees,
        "totaux": totaux,
        "ecarts": ecarts,
        "waterfall": wf,
        "ecart_non_explique": residu,
        "pluri": pluri,
        "journal": list(d.get("journal") or []),
        "racc": d.get("raccordement_n_moins_1"),
        "controles": controles,
        "bloquants": [c for c in controles if c.get("niveau") == "bloquant"],
        "phrase_synthese": _phrase_synthese(d, s, ecarts),
        "actions_soldees": len(soldees),
        "sous_execution": sous_execution,
    }


# Rendu ------------------------------------------------------------------

def rendre_html(bilan: dict[str, Any]) -> str:
    return _env().get_template(TEMPLATE_NAME).render(**preparer_contexte(bilan))


def rendre_pdf(bilan: dict[str, Any]) -> bytes:
    """Rend le PDF du bilan. Import WeasyPrint local : lourd au chargement."""
    from weasyprint import HTML

    html = rendre_html(bilan)
    return HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()


def nom_fichier(bilan: dict[str, Any]) -> str:
    d = bilan.get("donnees") or bilan
    return f"bilan-financier-{d.get('annee')}-v{bilan.get('version', 1)}.pdf"
