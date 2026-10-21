"""Générateur ICS des fenêtres d'action. Réutilisable par un futur flux d'abonnement.

Le mode « discret » omet le libellé de l'action dans le titre. L'export utilise « complet ».
Aucun montant ni prestataire n'est écrit, même s'il est présent dans la ligne source.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

ModeTitre = Literal["complet", "discret"]

_STATUT_ICS = {
    "a_confirmer": "TENTATIVE",
    "supprime": "CANCELLED",
}


def generer_ics(
    occurrences: list[dict[str, Any]],
    *,
    nom_projet: str,
    url_front: str,
    projet_id: str,
    mode_titre: ModeTitre = "complet",
) -> bytes:
    from icalendar import Calendar, Event

    cal = Calendar()
    cal.add("prodid", "-//KerERC//Calendrier//FR")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"KerERC – {nom_projet}")
    for occ in occurrences:
        for evenement in _evenements(occ, url_front, projet_id, mode_titre):
            ev = Event()
            for cle, valeur in evenement.items():
                ev.add(cle, valeur)
            cal.add_component(ev)
    return cal.to_ical()


def _evenements(
    occ: dict[str, Any],
    url_front: str,
    projet_id: str,
    mode_titre: ModeTitre,
) -> list[dict[str, Any]]:
    debut, fin = fenetre(occ)
    if debut is None or fin is None:
        return []
    identifiant = str(occ.get("id") or "")
    statut = str(occ.get("statut") or "")
    status = _STATUT_ICS.get(statut, "CONFIRMED")
    description = _description(occ, url_front, projet_id)
    titre = _titre_action(occ, mode_titre)
    if (fin - debut).days <= 31:
        return [
            _journee(
                uid=f"{identifiant}@kererc.fr",
                debut=debut,
                fin_inclusive=fin,
                summary=titre,
                description=description,
                status=status,
                categorie=statut,
            )
        ]
    return [
        _journee(
            uid=f"{identifiant}-ouverture@kererc.fr",
            debut=debut,
            fin_inclusive=debut,
            summary=_prefixe("Ouverture de fenêtre", titre, mode_titre),
            description=description,
            status=status,
            categorie=statut,
        ),
        _journee(
            uid=f"{identifiant}-echeance@kererc.fr",
            debut=fin,
            fin_inclusive=fin,
            summary=_prefixe("Échéance", titre, mode_titre),
            description=description,
            status=status,
            categorie=statut,
        ),
    ]


def fenetre(occ: dict[str, Any]) -> tuple[date | None, date | None]:
    annee = occ.get("annee")
    mois_debut = occ.get("mois_debut")
    mois_fin = occ.get("mois_fin")
    if not annee or not mois_debut or not mois_fin:
        return None, None
    annee_fin = int(annee) + (1 if occ.get("traverse_nouvel_an") else 0)
    debut = date(int(annee), int(mois_debut), 1)
    fin = _fin_de_mois(annee_fin, int(mois_fin))
    if fin < debut:
        fin = _fin_de_mois(int(annee) + 1, int(mois_fin))
    return debut, fin


def _fin_de_mois(annee: int, mois: int) -> date:
    if mois == 12:
        return date(annee, 12, 31)
    return date(annee, mois + 1, 1) - timedelta(days=1)


def _journee(
    *,
    uid: str,
    debut: date,
    fin_inclusive: date,
    summary: str,
    description: str,
    status: str,
    categorie: str,
) -> dict[str, Any]:
    return {
        "uid": uid,
        "dtstart": debut,
        "dtend": fin_inclusive + timedelta(days=1),
        "summary": summary,
        "description": description,
        "status": status,
        "categories": [categorie] if categorie else [],
        "transp": "TRANSPARENT",
    }


def _titre_action(occ: dict[str, Any], mode: ModeTitre) -> str:
    if mode == "discret":
        return "Intervention"
    return str(occ.get("titre") or occ.get("code") or "Action")


def _prefixe(prefixe: str, titre: str, mode: ModeTitre) -> str:
    if mode == "discret":
        return prefixe
    return f"{prefixe} – {titre}"


def _description(occ: dict[str, Any], url_front: str, projet_id: str) -> str:
    debut, fin = fenetre(occ)
    lien = (
        f"{url_front.rstrip('/')}/projets/{projet_id}"
        f"?vue=calendar&occurrence={occ.get('id')}"
    )
    lignes = [
        f"Mesure : {occ.get('mesure') or '—'}",
        f"UG : {occ.get('ug') or '—'}",
        f"Fenêtre : {debut.isoformat() if debut else '—'} → {fin.isoformat() if fin else '—'}",
        f"Statut : {occ.get('statut') or '—'}",
        lien,
    ]
    return "\n".join(lignes)
