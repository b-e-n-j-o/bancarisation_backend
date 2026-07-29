"""Rendu PDF du bilan de suivi écologique annuel."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "bilan_suivi.html.j2"

STATUT_LABELS = {
    "a_confirmer": "À confirmer",
    "planifie": "Planifiée",
    "en_cours": "En cours",
    "realise": "Réalisée",
    "repousse": "Reportée",
    "supprime": "Supprimée",
}

CATEGORIE_LABELS = {
    "MG": "Mesures de gestion",
    "SE": "Suivis écologiques",
    "TU": "Travaux et aménagements",
    "AD": "Administration et coordination",
}


def f_pct(v: Any) -> str:
    if v is None:
        return "—"
    return f"{float(v) * 100:.0f}\u202f%".replace(".", ",")


def f_dt(v: Any, heure: bool = False) -> str:
    if not v:
        return "—"
    try:
        s = str(v).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
    except ValueError:
        return str(v)[:10]
    return d.strftime("%d/%m/%Y à %Hh%M") if heure else d.strftime("%d/%m/%Y")


def f_statut_label(v: Any) -> str:
    return STATUT_LABELS.get(str(v), str(v) if v else "—")


def _grouper_par_categorie(lignes: list[dict]) -> list[tuple[str, list[dict]]]:
    groupes: dict[str, list[dict]] = {}
    for l in lignes:
        cat = str(l.get("categorie") or "AUTRE")
        groupes.setdefault(cat, []).append(l)
    out = []
    for cat in sorted(groupes):
        items = sorted(
            groupes[cat],
            key=lambda x: (str(x.get("code") or ""), str(x.get("titre") or "")),
        )
        out.append((CATEGORIE_LABELS.get(cat, cat), items))
    return out


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters.update(
        pct=f_pct,
        dt=f_dt,
        statut_label=f_statut_label,
    )
    return env


def preparer_contexte(bilan: dict[str, Any]) -> dict[str, Any]:
    d = bilan.get("donnees") or bilan
    meta = {
        "id": bilan.get("id"),
        "version": bilan.get("version", 1),
        "statut": bilan.get("statut", "genere"),
        "depose_le": bilan.get("depose_le"),
        "genere_par": bilan.get("genere_par"),
    }
    s = dict(d.get("synthese") or {})
    lignes = list(d.get("lignes") or [])
    controles = list(d.get("controles") or bilan.get("controles") or [])
    return {
        "d": d,
        "meta": meta,
        "s": s,
        "lignes": lignes,
        "lignes_par_categorie": _grouper_par_categorie(lignes),
        "journal": list(d.get("journal") or []),
        "controles": controles,
        "bloquants": [c for c in controles if c.get("niveau") == "bloquant"],
        "avertissements": [c for c in controles if c.get("niveau") == "avertissement"],
    }


def rendre_html(bilan: dict[str, Any]) -> str:
    return _env().get_template(TEMPLATE_NAME).render(**preparer_contexte(bilan))


def rendre_pdf(bilan: dict[str, Any]) -> bytes:
    from weasyprint import HTML

    html = rendre_html(bilan)
    return HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()


def nom_fichier(bilan: dict[str, Any]) -> str:
    d = bilan.get("donnees") or bilan
    return f"bilan-suivi-{d.get('annee')}-v{bilan.get('version', 1)}.pdf"
