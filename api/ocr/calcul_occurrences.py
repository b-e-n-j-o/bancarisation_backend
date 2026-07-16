"""
occurrences.py — Le moteur.

Déroule les échéances-templates en occurrences concrètes, UNE SEULE FOIS, à l'import.

⚠️ Nature du moteur : c'est un SEMOIR, pas une vue.
Une fois les occurrences matérialisées en base, elles appartiennent au user
(CRUD libre). Le moteur ne repasse jamais dessus : le re-jouer sur un dossier
déjà semé écraserait son travail. Voir ingestion_base_de_donnees.ingérer(replace=...)
pour le garde-fou.

Ce module est PUR (aucune I/O, aucun accès base) → testable directement.
Il remplace le générateur TS `echeancesToOccurrences.ts` du prototype : les
occurrences viennent désormais de la base, le frontend ne fait que les lire.

Usage standalone :
    python occurrences.py echeances_mistral.json
    python occurrences.py echeances_mistral.json --annee-fin 2048 --out occurrences.json
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from .models import Echeance, ExtractionResult, Occurrence, Recurrence, TypeRecurrence
from .ug_ids import normalize_ug_ids

_SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_INPUT = _SCRIPT_DIR / "echeances_mistral.json"
DEFAULT_OUTPUT = _SCRIPT_DIR / "occurrences.json"

# --- Helpers ---------------------------------------------------------------


def formater_code(code: str) -> str:
    """"TU3" -> "TU 3" (aligné sur l'affichage du frontend)."""
    m = re.match(r"^([A-Z]+)\s*(\d+)$", code.strip())
    return f"{m.group(1)} {m.group(2)}" if m else code


def _mois(mmdd: str | None) -> int | None:
    if not mmdd:
        return None
    try:
        mm = int(mmdd[:2])
    except ValueError:
        return None
    return mm if 1 <= mm <= 12 else None


def annee_fin_suggeree(echeances: list[Echeance]) -> int:
    """Horizon déduit des ancrages et de la durée globale du plan."""
    candidats = [date.today().year]
    ancrages = [e.recurrence.ancrage_annee for e in echeances if e.recurrence.ancrage_annee]
    durees = [e.duree_gestion_ans for e in echeances if e.duree_gestion_ans]

    if ancrages:
        candidats.append(max(ancrages))
        if durees:
            # Durée globale : début le plus ancien + horizon du plan − 1.
            candidats.append(min(ancrages) + max(durees) - 1)
    return max(candidats)


def annees_paliers(r: Recurrence, annee_fin: int) -> list[int]:
    """
    Cadence par paliers : l'ancrage est la 1ʳᵉ occurrence (état zéro), puis chaque
    palier émet ``nombre_occurrences`` dates espacées de ``intervalle_ans`` depuis la
    DERNIÈRE occurrence émise — pas depuis le début du segment calendaire.

    Validation documentaire (SE1, p.9) : 2019 → 2020…2023 (annuel ×4) → 2026 (2023+3).
    """
    debut = r.ancrage_annee
    if debut is None or not r.paliers:
        return []

    annees: list[int] = []
    derniere = debut

    if debut <= annee_fin:
        annees.append(debut)

    for palier in r.paliers:
        pas = max(1, round(palier.intervalle_ans))
        for _ in range(palier.nombre_occurrences):
            annee = derniere + pas
            if annee > annee_fin:
                return annees
            annees.append(annee)
            derniere = annee

    return annees


def annees_occurrences(e: Echeance, annee_fin: int) -> list[int]:
    """Années où l'échéance produit une occurrence. Liste vide = non plaçable."""
    r = e.recurrence
    debut = r.ancrage_annee
    if debut is None:
        return []

    if r.type in (TypeRecurrence.ponctuel, TypeRecurrence.dependant_evenement):
        return [debut] if debut <= annee_fin else []

    if r.type == TypeRecurrence.periodique:
        pas = max(1, round(r.intervalle_ans or 1))
        return list(range(debut, annee_fin + 1, pas))

    if r.type == TypeRecurrence.campagnes:
        # K passages/an pendant M ans : une occurrence par ANNÉE de campagne.
        # Le nombre de passages est porté dans le titre (pas de granularité
        # infra-annuelle dans le calendrier actuel).
        duree = max(1, r.duree_ans or 1)
        return [a for a in range(debut, debut + duree) if a <= annee_fin]

    if r.type == TypeRecurrence.paliers:
        return annees_paliers(r, annee_fin)

    return []


def statut_initial(annee: int, e: Echeance, annee_courante: int) -> str:
    """
    Cold-start :
      - ancrage déduit d'une frise (champs_a_confirmer) → 'a_confirmer'
      - occurrence passée → 'a_confirmer' (le user valide ce qui a été fait)
      - occurrence future → 'planifie'
    """
    if "ancrage_annee" in e.champs_a_confirmer:
        return "a_confirmer"
    return "a_confirmer" if annee < annee_courante else "planifie"


def titre_occurrence(e: Echeance) -> str:
    r = e.recurrence
    if r.type == TypeRecurrence.campagnes and r.occurrences_par_an:
        return f"{e.libelle} (×{r.occurrences_par_an}/an)"
    return e.libelle


# --- Moteur ----------------------------------------------------------------


def generer(
    echeances: list[Echeance],
    annee_fin: int,
    annee_courante: int | None = None,
) -> tuple[list[Occurrence], list[Echeance]]:
    """
    Retourne (occurrences, non_placables).

    non_placables = échéances sans ancrage (typiquement les 2e/3e éclaircies
    dépendantes d'un événement) → à caler à la main par le user, hors calendrier.
    """
    annee_courante = annee_courante or date.today().year
    occurrences: list[Occurrence] = []
    non_placables: list[Echeance] = []

    for e in echeances:
        annees = annees_occurrences(e, annee_fin)
        if not annees:
            non_placables.append(e)
            continue

        md = _mois(e.fenetre_intervention.debut)
        mf = _mois(e.fenetre_intervention.fin)
        code = formater_code(e.code_operation)
        titre = titre_occurrence(e)

        for annee in annees:
            occurrences.append(Occurrence(
                echeance_cle=e.id,
                annee=annee,
                code=code,
                titre=titre,
                categorie=e.type_operation,
                statut=statut_initial(annee, e, annee_courante),
                ug_ids=normalize_ug_ids(e.ug_ids),
                mois_debut=md,
                mois_fin=mf,
                traverse_nouvel_an=e.fenetre_intervention.traverse_nouvel_an,
                origine="ia",
                confiance=e.confiance,
                champs_a_confirmer=e.champs_a_confirmer,
                avertissements=e.avertissements,
            ))

    return occurrences, non_placables


def _resoudre_chemin(chemin: str | Path) -> Path:
    p = Path(chemin)
    if p.is_absolute():
        return p
    if p.exists():
        return p.resolve()
    candidat = _SCRIPT_DIR / p
    if candidat.exists():
        return candidat
    return p


def main() -> None:
    p = argparse.ArgumentParser(
        description="Génère les occurrences concrètes depuis un JSON d'échéances."
    )
    p.add_argument(
        "echeances",
        nargs="?",
        default=str(DEFAULT_INPUT),
        help="JSON produit par extract_echeances_* (défaut : echeances_mistral.json)",
    )
    p.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Fichier JSON de sortie")
    p.add_argument(
        "--annee-fin",
        type=int,
        default=None,
        help="Horizon de génération (défaut : déduit du plan)",
    )
    p.add_argument(
        "--annee-courante",
        type=int,
        default=None,
        help="Année de référence pour les statuts initiaux (défaut : aujourd'hui)",
    )
    args = p.parse_args()

    src = _resoudre_chemin(args.echeances)
    if not src.exists():
        sys.exit(f"❌ JSON introuvable : {src}")

    resultat = ExtractionResult.model_validate_json(src.read_text(encoding="utf-8"))
    echeances = resultat.echeances
    annee_fin = args.annee_fin or annee_fin_suggeree(echeances)

    occs, non_placables = generer(
        echeances,
        annee_fin=annee_fin,
        annee_courante=args.annee_courante,
    )

    out = _resoudre_chemin(args.out)
    if not out.is_absolute() and not Path(args.out).exists():
        out = _SCRIPT_DIR / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "annee_fin": annee_fin,
                "echeances_source": src.name,
                "nb_echeances": len(echeances),
                "nb_occurrences": len(occs),
                "nb_non_placables": len(non_placables),
                "occurrences": [o.model_dump(mode="json") for o in occs],
                "non_placables": [e.model_dump(mode="json") for e in non_placables],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"📥 {len(echeances)} échéance(s) chargée(s) depuis {src.name}")
    print(f"⚙️  {len(occs)} occurrence(s) générée(s) — horizon {annee_fin}")
    if non_placables:
        print(f"   {len(non_placables)} échéance(s) NON plaçable(s) :")
        for e in non_placables:
            print(f"   · {e.id:<42} {e.recurrence.regle_source or '(sans ancrage)'}")
    print(f"✅ → {out}")


if __name__ == "__main__":
    main()
