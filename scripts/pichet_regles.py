#!/usr/bin/env python3
"""Corrections one-shot des règles Pichet, puis régénération des occurrences.

Dry-run par défaut. ``--apply`` écrit les récurrences, les quatre règles
manquantes, puis régénère les occurrences non figées.

    python scripts/pichet_regles.py
    python scripts/pichet_regles.py -v
    python scripts/pichet_regles.py --apply
"""

from __future__ import annotations

import argparse
import calendar
import logging
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from api.calendrier.generation import planifier  # noqa: E402
from api.calendrier.recurrence import convertir, expand, to_phrase  # noqa: E402
from api.db.env import get_database_url  # noqa: E402

log = logging.getLogger("pichet_regles")

PROJET = "8d061654-c30c-4ced-95a6-99297b43020a"
ANNEE_N = 2025

NOUVELLES = (
    {
        "cle": "pichet-rg1-arrachage-eee",
        "code_operation": "RG1",
        "type_operation": "Restauration et Gestion",
        "libelle": "Arrachage manuel des espèces envahissantes",
        "ug_ids": ["ug1"],
        "fenetre_debut": "09-01",
        "fenetre_fin": "11-30",
        "recurrence": {
            "type": "dependant_evenement",
            "ancrage": "N",
            "annee_N": ANNEE_N,
            "paliers": [],
            "annees_explicites": [],
            "duree_ans": 30,
            "regle_source": "si développement constaté",
            "annees": [],
        },
    },
    {
        "cle": "pichet-rg2-regarnis",
        "code_operation": "RG2",
        "type_operation": "Restauration et Gestion",
        "libelle": "Regarnis des plantations",
        "ug_ids": ["ug2"],
        "fenetre_debut": None,
        "fenetre_fin": None,
        "recurrence": {
            "type": "dependant_evenement",
            "ancrage": "N",
            "annee_N": ANNEE_N,
            "paliers": [],
            "annees_explicites": [],
            "duree_ans": 30,
            "regle_source": "en cas de pertes importantes",
            "annees": [],
        },
    },
    {
        "cle": "pichet-fg2-revision",
        "code_operation": "FG2",
        "type_operation": "Fonctionnement Général",
        "libelle": "Révision du plan de gestion",
        "ug_ids": [],
        "fenetre_debut": None,
        "fenetre_fin": None,
        "recurrence": {
            "type": "dependant_evenement",
            "ancrage": "N",
            "annee_N": ANNEE_N,
            "paliers": [],
            "annees_explicites": [],
            "duree_ans": 30,
            "regle_source": "selon l'évolution des indicateurs",
            "annees": [],
        },
    },
    {
        "cle": "pichet-fg1-veille",
        "code_operation": "FG1",
        "type_operation": "Fonctionnement Général",
        "libelle": "Veille sur le site",
        "ug_ids": [],
        "fenetre_debut": None,
        "fenetre_fin": None,
        "recurrence": {
            "type": "non_precisee",
            "ancrage": "N",
            "annee_N": ANNEE_N,
            "paliers": [],
            "annees_explicites": [],
            "duree_ans": 30,
            "regle_source": "une dizaine de passages sur la durée du plan",
            "annees": [],
        },
    },
)


def _socle(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": rec.get("type") or "paliers",
        "ancrage": "N",
        "annee_N": ANNEE_N,
        "paliers": rec.get("paliers") or [],
        "annees_explicites": rec.get("annees_explicites") or [],
        "duree_ans": int(rec.get("duree_ans") or 30),
        "fenetre_annees": rec.get("fenetre_annees"),
        "regle_source": rec.get("regle_source"),
        "annees": [],
    }


def dernier_jour(fenetre_fin: str | None) -> str | None:
    """07-28 → 07-31. Février est calé sur 2024 (29)."""
    if not fenetre_fin or len(fenetre_fin) < 5:
        return fenetre_fin
    mois_txt, _, jour_txt = fenetre_fin.partition("-")
    try:
        mois = int(mois_txt)
        jour = int(jour_txt)
    except ValueError:
        return fenetre_fin
    if not 1 <= mois <= 12:
        return fenetre_fin
    fin = calendar.monthrange(2024, mois)[1]
    if jour == fin:
        return f"{mois:02d}-{jour:02d}"
    return f"{mois:02d}-{fin:02d}"


def corriger(ligne: dict[str, Any], rec: dict[str, Any]) -> tuple[dict[str, Any], str | None, list[str], list[str]]:
    """Retourne récurrence, hypothèse, champs à confirmer, et le motif de correction."""
    code = ligne["code_operation"]
    libelle = ligne["libelle"] or ""
    hypothese = ligne.get("hypothese")
    champs = list(ligne.get("champs_a_confirmer") or [])
    motifs: list[str] = []

    if code == "ES1" and "Passage de suivi" in libelle or code == "ES2":
        rec = _socle(rec)
        rec["type"] = "paliers"
        rec["paliers"] = [
            {"debut": 0, "fin": 5, "pas": 1},
            {"debut": 10, "fin": None, "pas": 5},
        ]
        rec["regle_source"] = (
            "annuel de N à N+5, puis tous les 5 ans jusqu'à N+30 (arrêté art. 14)"
        )
        motifs.append("paliers N..N+5 puis tous les 5 ans de N+10 à N+30")
    elif "activité continue" in libelle:
        rec = _socle(rec)
        rec["type"] = "continue"
        rec["paliers"] = []
        motifs.append("type continue")
    elif "Griffage du sol" in libelle:
        rec = _socle(rec)
        rec["type"] = "paliers"
        rec["paliers"] = [{"debut": 3, "fin": None, "pas": 2, "pas_max": 3}]
        hypothese = (
            "2 ans retenu (arrêté art. 12.5) ; "
            "plan de gestion : tous les 2 à 3 ans à partir de N+3"
        )
        motifs.append("griffage pas 2, pas_max 3, depuis N+3")
    elif "Entretien par débroussaillage" in libelle:
        hypothese = "fréquence non précisée par le plan de gestion"
        if "frequence" not in champs:
            champs.append("frequence")
        motifs.append("annuel conservé, hypothèse de fréquence")
    elif "Fauche d'été" in libelle:
        hypothese = "passage facultatif selon le plan"
        motifs.append("hypothèse fauche d'été")

    rec["annees"] = expand(rec)
    return rec, hypothese, champs, motifs


def _charger(cur: Any) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, cle, code_operation, type_operation, libelle, lib_thema, ug_ids,
               recurrence, fenetre_debut, fenetre_fin, fenetre_traverse_nouvel_an,
               confiance, statut, origine, hypothese, champs_a_confirmer
        FROM bancarisation.echeance
        WHERE projet_id = %s
        ORDER BY code_operation, libelle
        """,
        (PROJET,),
    )
    return [dict(ligne) for ligne in cur.fetchall()]


def _occurrences(cur: Any, echeance_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, annee, fige, date_realisation, titre, code, categorie, lib_thema,
               ug_ids, mois_debut, mois_fin, traverse_nouvel_an, statut, origine
        FROM bancarisation.occurrence
        WHERE echeance_id = %s
        """,
        (echeance_id,),
    )
    lignes = []
    for ligne in cur.fetchall():
        occ = dict(ligne)
        occ["id"] = str(occ["id"])
        lignes.append(occ)
    return lignes


def _preparer(ligne: dict[str, Any]) -> dict[str, Any]:
    rec = convertir(dict(ligne["recurrence"] or {}), ANNEE_N)
    rec, hypothese, champs, motifs = corriger(ligne, rec)
    fin = dernier_jour(ligne.get("fenetre_fin"))
    if fin != ligne.get("fenetre_fin"):
        motifs.append(f"fenêtre {ligne.get('fenetre_fin')} → {fin}")
    return {
        "id": str(ligne["id"]),
        "cle": ligne["cle"],
        "projet_id": PROJET,
        "code_operation": ligne["code_operation"],
        "type_operation": ligne["type_operation"],
        "libelle": ligne["libelle"],
        "lib_thema": ligne["lib_thema"],
        "ug_ids": list(ligne["ug_ids"] or []),
        "recurrence": rec,
        "fenetre_debut": ligne.get("fenetre_debut"),
        "fenetre_fin": fin,
        "fenetre_traverse_nouvel_an": bool(ligne.get("fenetre_traverse_nouvel_an")),
        "confiance": ligne.get("confiance"),
        "statut": ligne.get("statut") or "a_confirmer",
        "origine": ligne.get("origine") or "ia",
        "hypothese": hypothese,
        "champs_a_confirmer": champs,
        "motifs": motifs,
    }


def _rapport_lisible(prep: dict[str, Any], occurrences: list[dict[str, Any]]) -> str:
    echeance = {k: v for k, v in prep.items() if k != "motifs"}
    rapport = planifier(echeance, occurrences)
    par_id = {o["id"]: o for o in occurrences}
    suppr = []
    for identifiant in rapport.supprimees:
        occ = par_id.get(identifiant)
        if occ:
            suppr.append(str(occ["annee"]))
        else:
            suppr.append(identifiant)
    figes = []
    for identifiant in rapport.conservees:
        occ = par_id.get(identifiant)
        if not occ:
            continue
        realise = occ.get("date_realisation")
        marque = f"{occ['annee']}"
        if realise:
            marque += f" réalisé {realise}"
        if occ.get("statut") == "supprime":
            marque += " supprimé"
        figes.append(marque)
    phrase = to_phrase(
        prep["recurrence"],
        prep.get("fenetre_debut"),
        prep.get("fenetre_fin"),
        traverse_nouvel_an=prep["fenetre_traverse_nouvel_an"],
    )
    lignes = [
        f"{prep['code_operation']} — {prep['libelle']}",
        f"  phrase : {phrase}",
    ]
    if prep["motifs"]:
        lignes.append(f"  correction : {', '.join(prep['motifs'])}")
    if prep.get("hypothese"):
        lignes.append(f"  hypothèse : {prep['hypothese']}")
    lignes.append(
        "  régénération : "
        f"{len(rapport.creees)} créée(s) {sorted(c['annee'] for c in rapport.creees) or '—'}, "
        f"{len(rapport.mises_a_jour)} mise(s) à jour, "
        f"{len(rapport.supprimees)} supprimée(s) {sorted(suppr, key=lambda s: int(s) if s.isdigit() else 0) or '—'}, "
        f"{len(rapport.conservees)} figée(s) {figes or '—'}, "
        f"conflits {rapport.annees_en_conflit or '—'}"
    )
    return "\n".join(lignes)


def _ecrire(cur: Any, preparations: list[dict[str, Any]]) -> None:
    for prep in preparations:
        cur.execute(
            """
            UPDATE bancarisation.echeance
            SET recurrence = %s,
                fenetre_fin = %s,
                hypothese = %s,
                champs_a_confirmer = %s
            WHERE id = %s
            """,
            (
                Jsonb(prep["recurrence"]),
                prep["fenetre_fin"],
                prep["hypothese"],
                prep["champs_a_confirmer"],
                prep["id"],
            ),
        )


def _creer_manquantes(cur: Any, *, ecrire: bool) -> list[str]:
    textes = []
    for regle in NOUVELLES:
        cur.execute(
            "SELECT id FROM bancarisation.echeance WHERE projet_id = %s AND cle = %s",
            (PROJET, regle["cle"]),
        )
        existe = cur.fetchone()
        phrase = to_phrase(regle["recurrence"], regle["fenetre_debut"], regle["fenetre_fin"])
        if existe:
            textes.append(f"déjà là {regle['code_operation']} — {regle['libelle']} : {phrase}")
            continue
        textes.append(
            f"à créer {regle['code_operation']} — {regle['libelle']} : {phrase} "
            "(aucune occurrence)"
        )
        if not ecrire:
            continue
        cur.execute(
            """
            INSERT INTO bancarisation.echeance (
                projet_id, cle, code_operation, type_operation, type_metier, libelle,
                ug_ids, recurrence, fenetre_debut, fenetre_fin, confiance,
                statut, origine, champs_a_confirmer
            ) VALUES (
                %s, %s, %s, %s, 'autre', %s,
                %s, %s, %s, %s, 1,
                'a_confirmer', 'ia', '{}'::text[]
            )
            """,
            (
                PROJET,
                regle["cle"],
                regle["code_operation"],
                regle["type_operation"],
                regle["libelle"],
                regle["ug_ids"],
                Jsonb(regle["recurrence"]),
                regle["fenetre_debut"],
                regle["fenetre_fin"],
            ),
        )
    return textes


def executer(*, apply: bool) -> None:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            lignes = _charger(cur)
            preparations = [_preparer(ligne) for ligne in lignes]
            blocs = []
            for prep in preparations:
                occurrences = _occurrences(cur, prep["id"])
                blocs.append(_rapport_lisible(prep, occurrences))
            creations = _creer_manquantes(cur, ecrire=apply)
            if apply:
                _ecrire(cur, preparations)
        if apply:
            conn.commit()
        else:
            conn.rollback()
    for bloc in blocs:
        log.info("\n%s", bloc)
    for texte in creations:
        log.info("%s", texte)
    if not apply:
        log.info("dry-run : aucune écriture. Relancer avec --apply pour enregistrer puis régénérer.")
        return
    from api.calendrier.generation import regenerer_projet

    rapports = regenerer_projet(PROJET, dry_run=False)
    log.info("%s échéance(s) régénérée(s).", len(rapports))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Corrections one-shot des règles Pichet.")
    parser.add_argument("--apply", action="store_true", help="Écrit en base et régénère.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    executer(apply=args.apply)


if __name__ == "__main__":
    main()
