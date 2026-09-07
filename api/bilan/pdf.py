"""Rendu PDF du bilan de suivi écologique annuel."""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .photos_pdf import enrichir_photos
from .pieces_pdf import enrichir_pdfs, fusionner_pdfs_annexes

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
    "EP": "État de référence",
    "TU": "Travaux et aménagements",
    "TE": "Travaux d'entretien",
    "SE": "Suivis écologiques",
    "MG": "Mesures de gestion",
    "AD": "Administration et coordination",
}

MOIS_COURT = (
    "", "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
)


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


def f_periode_occ(l: Any) -> str:
    if not isinstance(l, dict):
        return "—"
    annee = l.get("annee")
    d = l.get("mois_debut")
    f = l.get("mois_fin")
    try:
        di = int(d) if d is not None else 0
        fi = int(f) if f is not None else 0
    except (TypeError, ValueError):
        di, fi = 0, 0
    dl = MOIS_COURT[di] if 1 <= di <= 12 else None
    fl = MOIS_COURT[fi] if 1 <= fi <= 12 else None
    if dl and fl:
        if l.get("traverse_nouvel_an") and annee:
            return f"{dl} {annee} → {fl} {int(annee) + 1}"
        if dl == fl:
            return f"{dl} {annee}" if annee else dl
        return f"{dl} – {fl} {annee}" if annee else f"{dl} – {fl}"
    if dl:
        return f"{dl} {annee}" if annee else dl
    return f"Exercice {annee}" if annee else "—"


def _volets(lignes: list[dict]) -> dict[str, list[dict]]:
    realisees: list[dict] = []
    non_realisees: list[dict] = []
    reprogrammees: list[dict] = []
    for l in lignes:
        v = l.get("volet")
        if not v:
            if l.get("sortie_exercice") or l.get("statut") == "repousse":
                v = "reprogrammee"
            elif l.get("statut") == "realise":
                v = "realisee"
            else:
                v = "non_realisee"
        if v == "realisee":
            realisees.append(l)
        elif v == "reprogrammee":
            reprogrammees.append(l)
        else:
            non_realisees.append(l)
    return {
        "realisees": realisees,
        "non_realisees": non_realisees,
        "reprogrammees": reprogrammees,
    }


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
        periode_occ=f_periode_occ,
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
    # Copie : photos / PDF (octets) ne doivent pas polluer le snapshot JSON.
    lignes = copy.deepcopy(list(d.get("lignes") or []))
    controles = list(d.get("controles") or bilan.get("controles") or [])
    volets = _volets(lignes)
    ordre = volets["realisees"] + volets["non_realisees"] + volets["reprogrammees"]
    annexe_photos = enrichir_photos(ordre)
    annexe_pdfs, pdfs_fusion = enrichir_pdfs(ordre)
    journal = list(d.get("journal") or [])
    num = 5 if journal else 4
    num_annexe_photos = num if annexe_photos else None
    if annexe_photos:
        num += 1
    num_annexe_pieces = num if annexe_pdfs else None
    return {
        "d": d,
        "meta": meta,
        "s": s,
        "lignes": lignes,
        "lignes_par_categorie": _grouper_par_categorie(lignes),
        "volets": volets,
        "lignes_n1": list(d.get("lignes_n1") or []),
        "journal": journal,
        "annexe_photos": annexe_photos,
        "annexe_pdfs": annexe_pdfs,
        "nb_photos": sum(len(g["photos"]) for g in annexe_photos),
        "nb_pdfs": sum(len(g["pdfs"]) for g in annexe_pdfs),
        "num_annexe": num_annexe_photos or (5 if journal else 4),
        "num_annexe_photos": num_annexe_photos,
        "num_annexe_pieces": num_annexe_pieces,
        "controles": controles,
        "bloquants": [c for c in controles if c.get("niveau") == "bloquant"],
        "avertissements": [c for c in controles if c.get("niveau") == "avertissement"],
        "_pdfs_fusion": pdfs_fusion,
    }


def _contexte_template(ctx: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in ctx.items() if not k.startswith("_")}


def rendre_html(bilan: dict[str, Any]) -> str:
    ctx = preparer_contexte(bilan)
    return _env().get_template(TEMPLATE_NAME).render(**_contexte_template(ctx))


def rendre_pdf(bilan: dict[str, Any]) -> bytes:
    from weasyprint import HTML

    ctx = preparer_contexte(bilan)
    html = _env().get_template(TEMPLATE_NAME).render(**_contexte_template(ctx))
    pdf = HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()
    return fusionner_pdfs_annexes(pdf, ctx.get("_pdfs_fusion") or [])


def nom_fichier(bilan: dict[str, Any]) -> str:
    d = bilan.get("donnees") or bilan
    return f"bilan-suivi-{d.get('annee')}-v{bilan.get('version', 1)}.pdf"
