"""
generation.py — Régénère les occurrences d'une échéance à partir de sa règle.

Une occurrence figée (``fige`` ou ``date_realisation`` renseignée) n'est jamais
supprimée ni écrasée.

L'index unique réel est partiel : ``(echeance_id, annee) WHERE origine = 'ia'``.
Une échéance = un passage (fauche de printemps et fauche d'été sont deux
échéances), donc une seule ligne ``ia`` par année et par échéance.

Usage ::

    python generation.py --projet <uuid> [--echeance <uuid>] [--dry-run] -v
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import UUID

log = logging.getLogger("calendrier.generation")

_CODE = re.compile(r"^([A-Za-z]+)\s*(\d+)$")


def formater_code(code: str | None) -> str:
    brut = (code or "").strip()
    m = _CODE.match(brut)
    return f"{m.group(1).upper()} {m.group(2)}" if m else brut


def _mois(mmdd: str | None) -> int | None:
    if not mmdd:
        return None
    try:
        mois = int(str(mmdd)[:2])
    except ValueError:
        return None
    return mois if 1 <= mois <= 12 else None


def _figee(occurrence: dict[str, Any]) -> bool:
    if occurrence.get("fige"):
        return True
    return occurrence.get("date_realisation") not in (None, "")


def champs_occurrence(echeance: dict[str, Any], annee: int) -> dict[str, Any]:
    """Champs d'une occurrence générée pour ``annee``."""
    from api.calendrier.recurrence import expand

    origine_regle = (echeance.get("origine") or "ia").strip()
    return {
        "annee": annee,
        "code": formater_code(echeance.get("code_operation")),
        "titre": echeance.get("libelle") or "",
        "categorie": echeance.get("type_operation") or "MG",
        "lib_thema": echeance.get("lib_thema") or "autre",
        "ug_ids": list(echeance.get("ug_ids") or []),
        "mois_debut": _mois(echeance.get("fenetre_debut")),
        "mois_fin": _mois(echeance.get("fenetre_fin")),
        "traverse_nouvel_an": bool(echeance.get("fenetre_traverse_nouvel_an")),
        "statut": "planifie" if echeance.get("statut") == "validee" else "a_confirmer",
        "origine": "ia" if origine_regle == "ia" else "regle",
        "confiance": echeance.get("confiance"),
        "fige": False,
        "_annees_regle": expand(echeance.get("recurrence") or {}),
    }


def _compare(occurrence: dict[str, Any], voulu: dict[str, Any]) -> bool:
    """Vrai si l'occurrence non figée diffère de la cible."""
    for cle in (
        "code", "titre", "categorie", "lib_thema", "mois_debut", "mois_fin",
        "traverse_nouvel_an", "statut", "origine",
    ):
        if occurrence.get(cle) != voulu[cle] and not (
            occurrence.get(cle) in (None, "") and voulu[cle] in (None, "")
        ):
            return True
    gauche = [str(u) for u in (occurrence.get("ug_ids") or [])]
    droite = [str(u) for u in voulu["ug_ids"]]
    return gauche != droite


class Rapport:
    """Bilan d'une régénération. Les listes détaillées servent à l'écriture."""

    def __init__(self, echeance_id: str, annees: list[int]) -> None:
        self.echeance_id = echeance_id
        self.annees = annees
        self.supprimees: list[str] = []
        self.creees: list[dict[str, Any]] = []
        self.mises_a_jour: list[dict[str, Any]] = []
        self.conservees: list[str] = []
        self.annees_en_conflit: list[int] = []

    def publique(self) -> dict[str, Any]:
        return {
            "echeance_id": self.echeance_id,
            "annees": self.annees,
            "supprimees": list(self.supprimees),
            "creees": [int(c["annee"]) for c in self.creees],
            "mises_a_jour": [int(m["annee"]) for m in self.mises_a_jour],
            "conservees": list(self.conservees),
            "annees_en_conflit": list(self.annees_en_conflit),
        }


def planifier(echeance: dict[str, Any], occurrences: list[dict[str, Any]]) -> Rapport:
    """Décide suppressions, créations et mises à jour. Aucune écriture."""
    from api.calendrier.recurrence import expand

    recurrence = dict(echeance.get("recurrence") or {})
    annees = expand(recurrence)
    recurrence["annees"] = annees
    echeance["recurrence"] = recurrence
    rapport = Rapport(str(echeance.get("id") or ""), annees)
    cibles = {annee: champs_occurrence(echeance, annee) for annee in annees}

    figes: dict[int, list[dict[str, Any]]] = {}
    libres: list[dict[str, Any]] = []
    for occurrence in occurrences:
        if _figee(occurrence):
            figes.setdefault(int(occurrence["annee"]), []).append(occurrence)
            if occurrence.get("id") is not None:
                rapport.conservees.append(str(occurrence["id"]))
        else:
            libres.append(occurrence)

    # Une suppression humaine (figée + statut supprime) occupe l'année : on ne la recrée pas.
    supprimees_main = {
        int(o["annee"])
        for o in occurrences
        if o.get("fige") and o.get("statut") == "supprime"
    }
    occupees = set(figes) | supprimees_main
    rapport.annees_en_conflit = sorted(
        annee for annee in annees if annee in occupees and annee not in supprimees_main
    )

    par_annee: dict[int, list[dict[str, Any]]] = {}
    for occurrence in libres:
        annee = int(occurrence["annee"])
        if annee not in cibles or annee in occupees:
            if occurrence.get("id") is not None:
                rapport.supprimees.append(str(occurrence["id"]))
            continue
        par_annee.setdefault(annee, []).append(occurrence)

    for annee, groupe in par_annee.items():
        garder = groupe[0]
        for doublon in groupe[1:]:
            if doublon.get("id") is not None:
                rapport.supprimees.append(str(doublon["id"]))
        voulu = {k: v for k, v in cibles[annee].items() if not k.startswith("_")}
        if garder.get("id") is None or _compare(garder, voulu):
            rapport.mises_a_jour.append({
                "id": None if garder.get("id") is None else str(garder["id"]),
                "annee": annee,
                "champs": voulu,
            })

    for annee, cible in cibles.items():
        if annee in occupees or annee in par_annee:
            continue
        rapport.creees.append({k: v for k, v in cible.items() if not k.startswith("_")})

    log.info(
        "échéance %s : %s année(s), %s à créer, %s à mettre à jour, %s à supprimer, "
        "%s figée(s), %s conflit(s)",
        rapport.echeance_id,
        len(annees),
        len(rapport.creees),
        len(rapport.mises_a_jour),
        len(rapport.supprimees),
        len(rapport.conservees),
        len(rapport.annees_en_conflit),
    )
    return rapport


def appliquer_memoire(
    occurrences: list[dict[str, Any]],
    rapport: Rapport,
) -> list[dict[str, Any]]:
    """Applique un rapport en mémoire (tests d'idempotence, sans base)."""
    supprimer = set(rapport.supprimees)
    reste = [dict(o) for o in occurrences if str(o.get("id")) not in supprimer]
    par_id = {str(o.get("id")): o for o in reste}
    for maj in rapport.mises_a_jour:
        if maj["id"] is None or maj["id"] not in par_id:
            continue
        par_id[maj["id"]].update(maj["champs"])
        par_id[maj["id"]]["fige"] = False
        par_id[maj["id"]]["date_realisation"] = None
    for indice, creee in enumerate(rapport.creees):
        reste.append({
            **creee,
            "id": f"nouveau-{creee['annee']}-{indice}",
            "fige": False,
            "date_realisation": None,
        })
    return reste


@contextmanager
def _connexion() -> Iterator[Any]:
    from psycopg.rows import dict_row

    from auth.deps import jwt_claims

    if jwt_claims.get():
        from api.db.utilisateur import connect_utilisateur

        with connect_utilisateur(row_factory=dict_row) as conn:
            yield conn
        return
    import psycopg

    from api.db.env import get_database_url

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        yield conn


def _lire_echeance(cur: Any, echeance_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT id, projet_id, code_operation, type_operation, libelle, lib_thema,
               ug_ids, recurrence, fenetre_debut, fenetre_fin, fenetre_traverse_nouvel_an,
               confiance, statut, origine
        FROM bancarisation.echeance
        WHERE id = %s
        """,
        (echeance_id,),
    )
    return cur.fetchone()


def _lire_occurrences(cur: Any, echeance_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, annee, fige, date_realisation, titre, code, categorie, lib_thema,
               ug_ids, mois_debut, mois_fin, traverse_nouvel_an, statut, origine
        FROM bancarisation.occurrence
        WHERE echeance_id = %s
        """,
        (echeance_id,),
    )
    return list(cur.fetchall())


def _appliquer(cur: Any, echeance: dict[str, Any], rapport: Rapport) -> None:
    from psycopg.types.json import Jsonb

    cur.execute(
        """
        UPDATE bancarisation.echeance
        SET recurrence = %s
        WHERE id = %s
        """,
        (Jsonb(echeance["recurrence"]), echeance["id"]),
    )
    if rapport.supprimees:
        cur.execute(
            "DELETE FROM bancarisation.occurrence WHERE id = ANY(%s::uuid[])",
            (rapport.supprimees,),
        )
    for maj in rapport.mises_a_jour:
        if not maj["id"]:
            continue
        champs = maj["champs"]
        cur.execute(
            """
            UPDATE bancarisation.occurrence
            SET annee = %s, code = %s, titre = %s, categorie = %s, lib_thema = %s,
                ug_ids = %s, mois_debut = %s, mois_fin = %s, traverse_nouvel_an = %s,
                statut = %s, origine = %s, confiance = %s, fige = false
            WHERE id = %s AND fige = false AND date_realisation IS NULL
            """,
            (
                champs["annee"], champs["code"], champs["titre"], champs["categorie"],
                champs["lib_thema"], champs["ug_ids"], champs["mois_debut"], champs["mois_fin"],
                champs["traverse_nouvel_an"], champs["statut"], champs["origine"],
                champs["confiance"], maj["id"],
            ),
        )
    for creee in rapport.creees:
        cur.execute(
            """
            INSERT INTO bancarisation.occurrence (
                projet_id, echeance_id, annee, code, titre, categorie, lib_thema,
                ug_ids, mois_debut, mois_fin, traverse_nouvel_an, statut, origine,
                confiance, fige
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, false
            )
            """,
            (
                echeance["projet_id"], echeance["id"], creee["annee"], creee["code"],
                creee["titre"], creee["categorie"], creee["lib_thema"], creee["ug_ids"],
                creee["mois_debut"], creee["mois_fin"], creee["traverse_nouvel_an"],
                creee["statut"], creee["origine"], creee["confiance"],
            ),
        )


def regenerer_occurrences(echeance_id: UUID | str, *, dry_run: bool = False) -> dict[str, Any]:
    """Recharge la règle, recalcule le cache ``annees``, réécrit les occurrences non figées."""
    from api.calendrier.recurrence import RecurrenceError

    identifiant = str(echeance_id)
    try:
        with _connexion() as conn:
            with conn.cursor() as cur:
                echeance = _lire_echeance(cur, identifiant)
                if not echeance:
                    raise LookupError(f"Échéance introuvable : {identifiant}")
                echeance = dict(echeance)
                echeance["id"] = str(echeance["id"])
                occurrences = [dict(o) for o in _lire_occurrences(cur, identifiant)]
                for occurrence in occurrences:
                    occurrence["id"] = str(occurrence["id"])
                try:
                    rapport = planifier(echeance, occurrences)
                except RecurrenceError:
                    raise
                log.info(
                    "%s %s — supprimées %s, créées %s, maj %s, figées %s, conflits %s",
                    "dry-run" if dry_run else "écriture",
                    identifiant,
                    rapport.supprimees,
                    [c["annee"] for c in rapport.creees],
                    [m["annee"] for m in rapport.mises_a_jour],
                    rapport.conservees,
                    rapport.annees_en_conflit,
                )
                if not dry_run:
                    _appliquer(cur, echeance, rapport)
            return rapport.publique()
    except RecurrenceError:
        raise


def regenerer_projet(projet_id: UUID | str, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Une transaction par échéance."""
    identifiant = str(projet_id)
    with _connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM bancarisation.echeance WHERE projet_id = %s ORDER BY code_operation",
                (identifiant,),
            )
            ids = [str(ligne["id"]) for ligne in cur.fetchall()]
    log.info("projet %s : %s échéance(s) à régénérer", identifiant, len(ids))
    return [regenerer_occurrences(eid, dry_run=dry_run) for eid in ids]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Régénère les occurrences depuis les règles d'échéance.")
    parser.add_argument("--projet", required=True)
    parser.add_argument("--echeance", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    if args.echeance:
        rapport = regenerer_occurrences(args.echeance, dry_run=args.dry_run)
        log.info("%s", rapport)
        return
    for rapport in regenerer_projet(args.projet, dry_run=args.dry_run):
        log.info("%s", rapport)


if __name__ == "__main__":
    _backend = str(__import__("pathlib").Path(__file__).resolve().parents[2])
    if _backend not in sys.path:
        sys.path.insert(0, _backend)
    main()
