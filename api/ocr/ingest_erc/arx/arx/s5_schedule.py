"""Étape 5 — Échéancier : échéances structurées + dates d'ancrage connues -> dates calculées.

Ancres : signature (lue dans l'acte), notification (sinon = signature, en hypothèse),
et toute date fournie (`--anchor demarrage_travaux=2025-12-05`, `fin_travaux_compensatoires=…`).
Pour les années relatives (N+4…), N = année de l'ancre indiquée par l'obligation.
Une ancre inconnue laisse l'échéance en libellé relatif (« 15 j avant démarrage des travaux »).
"""
from __future__ import annotations

import calendar
from datetime import date

from .schemas import EcheanceCalculee, FicheLLM, Obligation

ANCHOR_LABEL = {
    "notification": "la notification", "signature": "la signature", "publication": "la publication",
    "demarrage_travaux": "le démarrage des travaux", "fin_travaux": "la fin des travaux",
    "demarrage_travaux_compensatoires": "le démarrage des travaux compensatoires",
    "fin_travaux_compensatoires": "la fin des travaux compensatoires", "livraison": "la livraison",
    "mise_en_service": "la mise en service",
}
UNIT = {"jour": "j", "semaine": "sem.", "mois": "mois", "an": "an(s)"}
ANCHOR_YEAR = {
    "notification": "année de notification", "signature": "année de signature", "publication": "année de publication",
    "demarrage_travaux": "année de démarrage des travaux", "fin_travaux": "année de fin des travaux",
    "demarrage_travaux_compensatoires": "année de démarrage des travaux compensatoires",
    "fin_travaux_compensatoires": "année d'achèvement des travaux compensatoires", "livraison": "année de livraison",
}


def _fr(iso: str | None) -> str:
    d = _d(iso)
    return d.strftime("%d/%m/%Y") if d else (iso or "?")


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _add(d: date, value: int, unit: str, sign: int) -> date:
    v = sign * value
    if unit == "jour":
        return date.fromordinal(d.toordinal() + v)
    if unit == "semaine":
        return date.fromordinal(d.toordinal() + 7 * v)
    months = v * (12 if unit == "an" else 1)
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def _dm(day_month: str, year: int) -> date | None:
    try:
        m, d = (int(x) for x in day_month.split("-"))
        return date(year, m, min(d, calendar.monthrange(year, m)[1]))
    except (ValueError, AttributeError):
        return None


def resolve_anchors(fiche: FicheLLM, given: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    anchors, hyps = dict(given), {}
    if fiche.date_signature:
        anchors.setdefault("signature", fiche.date_signature)
    if "notification" not in anchors and "signature" in anchors:
        anchors["notification"] = anchors["signature"]
        hyps["notification"] = "notification supposée à la date de signature"
    return anchors, hyps


def schedule(obls: list[Obligation], fiche: FicheLLM, given: dict[str, str]) -> tuple[list[EcheanceCalculee], dict]:
    anchors, hyps = resolve_anchors(fiche, given)
    rows = []
    for o in obls:
        e = o.echeance
        r = EcheanceCalculee(obligation_id=o.id, article=o.article, intitule=o.intitule,
                             preuve_attendue=list(o.preuve_attendue), statut=o.statut)
        anchor_name = e.anchor or ""
        a_label = e.anchor_label or ANCHOR_LABEL.get(anchor_name, anchor_name)
        a_date = _d(anchors.get(anchor_name))
        r.ancre = anchor_name
        r.hypothese = hyps.get(anchor_name, "")
        if e.kind == "date_fixe":
            r.date, r.calculable, r.libelle = e.date, bool(e.date), f"au plus tard le {_fr(e.date)}"
        elif e.kind in ("delai_apres", "delai_avant") and e.offset_value is not None and e.offset_unit:
            sign = 1 if e.kind == "delai_apres" else -1
            delay = f"{e.offset_value} {UNIT[e.offset_unit]} " if e.offset_value else ""
            r.libelle = f"{delay}{'après' if sign > 0 else 'avant'} {a_label}".strip()
            if a_date and anchor_name not in ("evenement_recurrent", "evenement_ponctuel", "autre"):
                r.date, r.calculable = _add(a_date, e.offset_value, e.offset_unit, sign).isoformat(), True
        elif e.kind == "date_annuelle" and e.day_month:
            off = e.year_offset or 0
            r.libelle = f"le {e.day_month[3:]}/{e.day_month[:2]} " + (f"de l'année suivant {a_label}" if off == 1 else
                                                                    f"de l'année de {a_label}" if a_label else "chaque année concernée")
            if a_date and anchor_name not in ("evenement_recurrent",):
                d = _dm(e.day_month, a_date.year + off)
                r.date, r.calculable = (d.isoformat() if d else None), bool(d)
        elif e.kind == "annees_relatives" and e.years_relative:
            n_label = ANCHOR_YEAR.get(anchor_name) or (f"année de « {a_label} »" if a_label else "")
            r.libelle = ", ".join("N" if y == 0 else f"N+{y}" for y in e.years_relative) + (f" (N = {n_label})" if n_label else "")
            if e.day_month:
                r.libelle += f", le {e.day_month[3:]}/{e.day_month[:2]}"
            if a_date:
                for y in e.years_relative:
                    d = _dm(e.day_month, a_date.year + y) if e.day_month else None
                    r.dates.append(d.isoformat() if d else str(a_date.year + y))
                r.date, r.calculable = r.dates[0], True
        elif e.kind in ("periode_autorisee", "periode_interdite"):
            w = f"{_fmt(e.window_start)} → {_fmt(e.window_end)}"
            r.fenetre = ("autorisé " if e.kind == "periode_autorisee" else "interdit ") + w
            r.libelle = r.fenetre
        elif e.kind == "permanente":
            r.libelle = e.texte or "en permanence"
        elif e.kind == "sans_delai":
            r.libelle = "sans délai" + (f" — {o.condition}" if o.condition else "")
        else:
            r.libelle = e.texte or "échéance non précisée"
        if o.condition and e.kind != "sans_delai":
            r.libelle += f" — si : {o.condition}"
        rows.append(r)
    rows.sort(key=lambda r: (r.date is None, r.date or "", r.article))
    return rows, anchors


def _fmt(mmdd: str | None) -> str:
    return f"{mmdd[3:]}/{mmdd[:2]}" if mmdd and len(mmdd) == 5 else "?"
