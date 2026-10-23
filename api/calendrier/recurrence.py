"""
recurrence.py — Règle de récurrence d'une échéance.

La règle (``echeance.recurrence``) est la donnée de référence.
``annees`` n'est qu'un cache recalculé par :func:`expand`.

Format cible ::

    {
      "type": "paliers",          # explicite | paliers | continue | dependant_evenement | non_precisee
      "ancrage": "N",
      "annee_N": 2025,            # année calendaire de N
      "paliers": [                # années RELATIVES à N
        {"debut": 0, "fin": 5, "pas": 1},
        {"debut": 10, "fin": null, "pas": 5, "pas_max": null}
      ],
      "annees_explicites": [],    # relatives à N ; un type paliers peut en porter
      "duree_ans": 30,            # fin du plan = N + duree_ans
      "fenetre_annees": null,     # dependant_evenement : [1, 3]
      "regle_source": "…",
      "annees": [2025, 2026]      # CACHE
    }

``pas_max`` > ``pas`` décrit une fourchette (« tous les 2 à 3 ans ») :
:func:`expand` retient ``pas``. L'hypothèse retenue vit dans ``echeance.hypothese``.

Les anciens champs (``intervalle_ans``, ``occurrences_par_an``, ``ancrage_annee``,
``annee_fin``, paliers ``{intervalle_ans, nombre_occurrences}``) sont encore lus
par :func:`expand` et :func:`convertir`, puis ne sont plus réécrits.

Extraction LLM encore en service (à ne pas confondre avec ce module) :
``api.ocr.ingest_erc.plan_de_gestion_to_calendrier.pgcal.s4_extract``,
appelée par ``passes/passe3.py``. Le semeur historique est
``api.ocr.calcul_occurrences`` — il ne régénère pas un calendrier déjà édité.

Usage ::

    python recurrence.py --help
    python recurrence.py convertir --projet <uuid> [--dry-run] [--annee-n 2025]
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from typing import Any

log = logging.getLogger("calendrier.recurrence")

TYPES = ("explicite", "paliers", "continue", "dependant_evenement", "non_precisee")
TYPES_SANS_ANNEES = ("dependant_evenement", "non_precisee")
DUREE_DEFAUT = 30

_MOIS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


class RecurrenceError(ValueError):
    """Règle illisible (annee_N absente, pas invalide, type inconnu)."""


def _annee_n(recurrence: dict[str, Any]) -> int | None:
    if recurrence.get("annee_N") is not None:
        return int(recurrence["annee_N"])
    if recurrence.get("ancrage_annee") is not None:
        return int(recurrence["ancrage_annee"])
    return None


def _exiger_annee_n(recurrence: dict[str, Any]) -> int:
    annee = _annee_n(recurrence)
    if annee is None:
        raise RecurrenceError(
            "annee_N manquant : impossible de dérouler la récurrence "
            "(ni annee_N, ni ancrage_annee)."
        )
    return annee


def _duree(recurrence: dict[str, Any]) -> int:
    brut = recurrence.get("duree_ans")
    if brut is None:
        return DUREE_DEFAUT
    return int(brut)


def _fin_ancienne(recurrence: dict[str, Any], annee_n: int) -> int:
    if recurrence.get("annee_fin") is not None:
        return int(recurrence["annee_fin"])
    if recurrence.get("duree_ans") is not None:
        return annee_n + int(recurrence["duree_ans"]) - 1
    return annee_n + DUREE_DEFAUT


def _paliers_nouveaux(recurrence: dict[str, Any]) -> bool:
    paliers = recurrence.get("paliers") or []
    if not paliers:
        return "annees_explicites" in recurrence and recurrence.get("type") == "paliers"
    return all(("pas" in p or "debut" in p) and "intervalle_ans" not in p for p in paliers)


def _tri(annees: list[int]) -> list[int]:
    return sorted(set(annees))


def _expand_explicite(recurrence: dict[str, Any], annee_n: int) -> list[int]:
    if recurrence.get("annees_explicites"):
        return _tri(annee_n + int(a) for a in recurrence["annees_explicites"])
    brutes = [int(a) for a in (recurrence.get("annees") or [])]
    if not brutes:
        return []
    if any(a >= 1900 for a in brutes):
        return _tri(brutes)
    return _tri(annee_n + a for a in brutes)


def _expand_paliers_nouveaux(recurrence: dict[str, Any], annee_n: int) -> list[int]:
    duree = _duree(recurrence)
    relatifs: set[int] = set()
    for palier in recurrence.get("paliers") or []:
        debut = int(palier.get("debut") or 0)
        fin = duree if palier.get("fin") is None else int(palier["fin"])
        pas = int(palier.get("pas") or 1)
        if pas < 1:
            raise RecurrenceError(f"pas invalide ({pas}) : un palier avance d'au moins 1 an.")
        for relatif in range(debut, fin + 1, pas):
            if 0 <= relatif <= duree:
                relatifs.add(relatif)
    for explicite in recurrence.get("annees_explicites") or []:
        relatif = int(explicite)
        if 0 <= relatif <= duree:
            relatifs.add(relatif)
    return _tri(annee_n + relatif for relatif in relatifs)


def _expand_paliers_anciens(recurrence: dict[str, Any], annee_n: int) -> list[int]:
    """Ancien moteur : l'ancrage est émis, puis chaque palier s'enchaîne depuis la dernière date."""
    fin = _fin_ancienne(recurrence, annee_n)
    annees: list[int] = []
    derniere = annee_n
    if annee_n <= fin:
        annees.append(annee_n)
    for palier in recurrence.get("paliers") or []:
        pas = max(1, round(float(palier.get("intervalle_ans") or 1)))
        for _ in range(int(palier.get("nombre_occurrences") or 0)):
            annee = derniere + pas
            if annee > fin:
                return annees
            annees.append(annee)
            derniere = annee
    return annees


def _expand_periodique(recurrence: dict[str, Any], annee_n: int) -> list[int]:
    pas = max(1, round(float(recurrence.get("intervalle_ans") or 1)))
    fin = _fin_ancienne(recurrence, annee_n)
    if annee_n > fin:
        return []
    return list(range(annee_n, fin + 1, pas))


def expand(recurrence: dict[str, Any] | None) -> list[int]:
    """Années calendaires de la règle, triées, sans doublon."""
    r = recurrence or {}
    type_ = r.get("type") or "non_precisee"
    if type_ in TYPES_SANS_ANNEES:
        return []
    annee_n = _exiger_annee_n(r)
    if type_ == "continue":
        return list(range(annee_n, annee_n + _duree(r) + 1))
    if type_ == "paliers" and _paliers_nouveaux(r):
        return _expand_paliers_nouveaux(r, annee_n)
    if type_ == "paliers":
        return _expand_paliers_anciens(r, annee_n)
    if type_ == "explicite":
        return _expand_explicite(r, annee_n)
    if type_ == "periodique":
        return _expand_periodique(r, annee_n)
    if type_ == "ponctuel":
        return [annee_n]
    if type_ == "campagnes":
        duree = max(1, int(r.get("duree_ans") or 1))
        fin = _fin_ancienne(r, annee_n)
        return [a for a in range(annee_n, annee_n + duree) if a <= fin]
    raise RecurrenceError(f"Type de récurrence inconnu : {type_}.")


def _relatif(n: int) -> str:
    if n == 0:
        return "N"
    if n > 0:
        return f"N+{n}"
    return f"N{n}"


def _bout_fenetre(mmdd: str | None) -> str | None:
    if not mmdd or not str(mmdd).strip():
        return None
    morceaux = str(mmdd).strip().split("-")
    try:
        mois = int(morceaux[0])
        jour = int(morceaux[1]) if len(morceaux) > 1 else 1
    except ValueError:
        return None
    if not 1 <= mois <= 12:
        return None
    nom = _MOIS[mois - 1]
    if jour <= 1 or jour >= 28:
        return nom
    if 10 <= jour <= 20:
        return f"mi-{nom}"
    return f"{jour} {nom}"


def phrase_fenetre(
    fenetre_debut: str | None,
    fenetre_fin: str | None,
    traverse_nouvel_an: bool = False,
) -> str:
    debut = _bout_fenetre(fenetre_debut)
    fin = _bout_fenetre(fenetre_fin)
    if not debut and not fin:
        return ""
    if debut and fin and debut != fin:
        texte = f"{debut} à {fin}"
    else:
        texte = debut or fin or ""
    if traverse_nouvel_an:
        texte += " (à cheval sur deux années)"
    return texte


def _phrase_palier(palier: dict[str, Any], duree: int, *, premier: bool) -> str:
    debut = int(palier.get("debut") or 0)
    fin = duree if palier.get("fin") is None else int(palier["fin"])
    pas = int(palier.get("pas") or 1)
    pas_max = palier.get("pas_max")
    dlab = _relatif(debut)
    flab = _relatif(fin)
    if pas_max is not None and int(pas_max) > pas:
        return (
            f"Tous les {pas} à {int(pas_max)} ans à partir de {dlab} ({pas} ans retenu)"
        )
    if pas == 1 and debut == fin:
        coeur = f"En {dlab}"
    elif pas == 1:
        coeur = f"Chaque année de {dlab} à {flab}"
    else:
        coeur = f"tous les {pas} ans de {dlab} à {flab}"
        if premier:
            coeur = coeur[0].upper() + coeur[1:]
        return coeur
    if not premier:
        coeur = coeur[0].lower() + coeur[1:]
    return coeur


def _phrase_liste_relative(relatifs: list[int], annee_n: int | None) -> str:
    ys = sorted(set(int(a) for a in relatifs))
    if not ys:
        return "Aucune année"
    if len(ys) == 1 and annee_n is not None:
        return f"En {_relatif(ys[0])} ({annee_n + ys[0]})"
    if len(ys) == 1:
        return f"En {_relatif(ys[0])}"

    groupes: list[list[int]] = []
    i = 0
    while i < len(ys):
        j = i
        while j + 1 < len(ys) and ys[j + 1] == ys[j] + 1:
            j += 1
        groupes.append(ys[i : j + 1])
        i = j + 1

    textes: list[str] = []
    isolats: list[int] = []

    def vider_isolats() -> None:
        if not isolats:
            return
        labels = ", ".join(_relatif(y) for y in isolats)
        textes.append(f"puis en {labels}" if textes else f"En {labels}")
        isolats.clear()

    for groupe in groupes:
        if len(groupe) >= 3:
            vider_isolats()
            texte = f"Chaque année de {_relatif(groupe[0])} à {_relatif(groupe[-1])}"
            if textes:
                texte = texte[0].lower() + texte[1:]
            textes.append(texte)
        else:
            isolats.extend(groupe)
    vider_isolats()
    return ", ".join(textes)


def _relatifs_affichage(recurrence: dict[str, Any]) -> tuple[list[int], int | None]:
    annee_n = _annee_n(recurrence)
    if recurrence.get("annees_explicites"):
        return [int(a) for a in recurrence["annees_explicites"]], annee_n
    brutes = [int(a) for a in (recurrence.get("annees") or [])]
    if annee_n is not None and brutes and any(a >= 1900 for a in brutes):
        return [a - annee_n for a in brutes], annee_n
    if brutes and annee_n is not None and all(a < 1900 for a in brutes):
        return brutes, annee_n
    return brutes, annee_n


def _corps(recurrence: dict[str, Any]) -> str:
    type_ = recurrence.get("type") or "non_precisee"
    source = (recurrence.get("regle_source") or "").strip()

    if type_ == "continue":
        return "En continu sur toute la durée du plan"
    if type_ == "dependant_evenement":
        fenetre = recurrence.get("fenetre_annees") or []
        if len(fenetre) >= 2:
            return (
                f"Si nécessaire, entre {_relatif(int(fenetre[0]))} et "
                f"{_relatif(int(fenetre[1]))} : {source}"
            ).rstrip()
        return f"Si nécessaire : {source}".rstrip()
    if type_ == "non_precisee":
        return f"Fréquence non précisée : {source}".rstrip()
    if type_ == "paliers" and _paliers_nouveaux(recurrence):
        duree = _duree(recurrence)
        morceaux: list[str] = []
        for i, palier in enumerate(recurrence.get("paliers") or []):
            morceau = _phrase_palier(palier, duree, premier=(i == 0))
            if i:
                morceau = "puis " + morceau[0].lower() + morceau[1:]
            morceaux.append(morceau)
        isolats = [int(a) for a in (recurrence.get("annees_explicites") or [])]
        if isolats:
            suite = _phrase_liste_relative(isolats, _annee_n(recurrence))
            if morceaux and suite.lower().startswith("en "):
                morceaux.append("puis " + suite[0].lower() + suite[1:])
            elif suite:
                morceaux.append(suite)
        if morceaux:
            return ", ".join(morceaux)
        return source or "Récurrence vide"
    if type_ in ("explicite", "ponctuel") or (
        type_ == "paliers" and not (recurrence.get("paliers") or [])
    ):
        if type_ == "ponctuel":
            annee_n = _annee_n(recurrence)
            return _phrase_liste_relative([0], annee_n)
        relatifs, annee_n = _relatifs_affichage(recurrence)
        if relatifs:
            return _phrase_liste_relative(relatifs, annee_n)
        return source or "Années non précisées"
    if type_ == "periodique":
        pas = max(1, round(float(recurrence.get("intervalle_ans") or 1)))
        annee_n = _annee_n(recurrence)
        fin = recurrence.get("annee_fin")
        if pas == 1 and annee_n is not None and fin is not None:
            return f"Chaque année de N à {_relatif(int(fin) - annee_n)}"
        if pas == 1:
            return "Chaque année"
        return f"Tous les {pas} ans"
    if type_ == "campagnes":
        fois = recurrence.get("occurrences_par_an")
        base = "Campagnes répétées"
        if fois:
            base = f"{fois} passages par an"
        return base
    if type_ == "paliers":
        return source or "Cadence par paliers"
    return source or "Récurrence à préciser"


def _compacter_annees(annees: list[int]) -> str:
    if not annees:
        return ""
    plages: list[tuple[int, int]] = []
    debut = precedent = annees[0]
    for annee in annees[1:]:
        if annee == precedent + 1:
            precedent = annee
            continue
        plages.append((debut, precedent))
        debut = precedent = annee
    plages.append((debut, precedent))
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in plages)


def to_phrase(
    recurrence: dict[str, Any] | None,
    fenetre_debut: str | None = None,
    fenetre_fin: str | None = None,
    *,
    traverse_nouvel_an: bool = False,
    avec_annees: bool = False,
) -> str:
    """Phrase lisible. ``avec_annees`` ajoute les années calendaires entre parenthèses."""
    r = recurrence or {}
    texte = _corps(r)
    fenetre = phrase_fenetre(fenetre_debut, fenetre_fin, traverse_nouvel_an)
    if fenetre:
        texte = f"{texte} — {fenetre}"
    if avec_annees:
        try:
            annees = expand(r)
        except RecurrenceError:
            annees = []
        compact = _compacter_annees(annees)
        if compact and f"({compact})" not in texte:
            texte = f"{texte} ({compact})"
    return texte


def hypothese_pas(recurrence: dict[str, Any] | None) -> str | None:
    """Texte d'hypothèse quand une fourchette ``pas`` / ``pas_max`` est arbitrée."""
    morceaux: list[str] = []
    for palier in (recurrence or {}).get("paliers") or []:
        pas = palier.get("pas")
        pas_max = palier.get("pas_max")
        if pas is None or pas_max is None:
            continue
        if int(pas_max) > int(pas):
            morceaux.append(
                f"{int(pas)} ans retenu (document : tous les {int(pas)} à {int(pas_max)} ans)"
            )
    return " ; ".join(morceaux) or None


def detecter_series(relatifs: list[int]) -> tuple[list[dict[str, Any]], list[int]]:
    """Séries arithmétiques maximales (≥ 3), prises sur des tranches contiguës."""
    reste = sorted(set(int(a) for a in relatifs))
    paliers: list[dict[str, Any]] = []
    while True:
        meilleur: tuple[int, int, int, int] | None = None
        for i in range(len(reste)):
            for j in range(i + 2, len(reste)):
                tranche = reste[i : j + 1]
                pas = [tranche[k + 1] - tranche[k] for k in range(len(tranche) - 1)]
                if len(set(pas)) == 1 and pas[0] > 0:
                    longueur = len(tranche)
                    if meilleur is None or longueur > meilleur[0] or (
                        longueur == meilleur[0] and i < meilleur[1]
                    ):
                        meilleur = (longueur, i, j, pas[0])
        if meilleur is None:
            break
        _, i, j, pas = meilleur
        tranche = reste[i : j + 1]
        paliers.append({"debut": tranche[0], "fin": tranche[-1], "pas": pas})
        reste = reste[:i] + reste[j + 1 :]
    paliers.sort(key=lambda p: (p["debut"], p["pas"]))
    return paliers, reste


def _structure_nouvelle(recurrence: dict[str, Any]) -> bool:
    type_ = recurrence.get("type")
    if type_ == "paliers":
        return _paliers_nouveaux(recurrence) and _annee_n(recurrence) is not None
    if type_ == "explicite":
        return "annees_explicites" in recurrence and recurrence.get("annee_N") is not None
    if type_ in ("continue", "dependant_evenement", "non_precisee"):
        return recurrence.get("annee_N") is not None or type_ in TYPES_SANS_ANNEES
    return False


def _socle(src: dict[str, Any], annee_n: int | None) -> dict[str, Any]:
    return {
        "type": src.get("type") or "non_precisee",
        "ancrage": src.get("ancrage") or "N",
        "annee_N": annee_n,
        "paliers": [],
        "annees_explicites": [],
        "duree_ans": int(src["duree_ans"]) if src.get("duree_ans") is not None else DUREE_DEFAUT,
        "fenetre_annees": src.get("fenetre_annees"),
        "regle_source": src.get("regle_source"),
        "annees": [],
    }


def convertir(recurrence: dict[str, Any], annee_N: int | None = None) -> dict[str, Any]:
    """Passe une règle (souvent ``explicite`` absolu) au format paliers, sans perdre une année."""
    src = copy.deepcopy(recurrence or {})
    if annee_N is not None:
        src["annee_N"] = int(annee_N)
    type_ = src.get("type") or "non_precisee"
    annee_n = _annee_n(src)

    if type_ in TYPES_SANS_ANNEES:
        out = _socle(src, annee_n)
        out["type"] = type_
        out["annees"] = []
        return out

    if type_ == "continue":
        if annee_n is None:
            raise RecurrenceError("annee_N manquant pour une récurrence continue.")
        out = _socle(src, annee_n)
        out["type"] = "continue"
        out["annees"] = expand(out)
        return out

    if _structure_nouvelle(src) and type_ in ("paliers", "explicite"):
        out = _socle(src, annee_n)
        out["type"] = type_
        out["paliers"] = [
            {
                "debut": int(p.get("debut") or 0),
                "fin": None if p.get("fin") is None else int(p["fin"]),
                "pas": int(p.get("pas") or 1),
                **(
                    {"pas_max": int(p["pas_max"])}
                    if p.get("pas_max") is not None
                    else {}
                ),
            }
            for p in (src.get("paliers") or [])
        ]
        out["annees_explicites"] = [int(a) for a in (src.get("annees_explicites") or [])]
        out["annees"] = expand(out)
        return out

    if annee_n is None:
        raise RecurrenceError("annee_N manquant : préciser annee_N ou ancrage_annee.")

    absolues = expand(src)
    relatifs = [a - annee_n for a in absolues]
    if any(r < 0 for r in relatifs):
        raise RecurrenceError(
            f"Des années précèdent N ({annee_n}) : {sorted(a for a in absolues if a < annee_n)}."
        )
    paliers, isolats = detecter_series(relatifs)
    out = _socle(src, annee_n)
    plancher = max(relatifs) if relatifs else 0
    if src.get("duree_ans") is not None:
        out["duree_ans"] = max(int(src["duree_ans"]), plancher)
    elif src.get("annee_fin") is not None:
        out["duree_ans"] = max(int(src["annee_fin"]) - annee_n, plancher)
    else:
        out["duree_ans"] = max(DUREE_DEFAUT, plancher)
    if paliers:
        out["type"] = "paliers"
        out["paliers"] = paliers
        out["annees_explicites"] = isolats
    else:
        out["type"] = "explicite"
        out["annees_explicites"] = relatifs
    out["annees"] = expand(out)
    return out


def _charger_projet(projet_id: str) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    from api.db.env import get_database_url

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, code_operation, libelle, recurrence,
                       fenetre_debut, fenetre_fin, fenetre_traverse_nouvel_an
                FROM bancarisation.echeance
                WHERE projet_id = %s
                ORDER BY code_operation, libelle
                """,
                (projet_id,),
            )
            return list(cur.fetchall())


def _ecrire_converties(lignes: list[tuple[Any, dict[str, Any]]]) -> None:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    from api.db.env import get_database_url

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            for echeance_id, recurrence in lignes:
                cur.execute(
                    """
                    UPDATE bancarisation.echeance
                    SET recurrence = %s
                    WHERE id = %s
                    """,
                    (Jsonb(recurrence), echeance_id),
                )
        conn.commit()


def convertir_projet(projet_id: str, *, annee_n: int | None = None, dry_run: bool = True) -> int:
    """Convertit les échéances d'un projet. N'écrit qu'en l'absence de ``dry_run``."""
    lignes = _charger_projet(projet_id)
    log.info("%s échéance(s) lue(s) pour %s", len(lignes), projet_id)
    a_ecrire: list[tuple[Any, dict[str, Any]]] = []
    for ligne in lignes:
        avant = ligne["recurrence"] or {}
        try:
            apres = convertir(avant, annee_n)
        except RecurrenceError as exc:
            log.error("%s %s — %s", ligne["code_operation"], ligne["id"], exc)
            continue
        phrase = to_phrase(
            apres,
            ligne.get("fenetre_debut"),
            ligne.get("fenetre_fin"),
            traverse_nouvel_an=bool(ligne.get("fenetre_traverse_nouvel_an")),
        )
        log.info(
            "%s %s\n  avant : %s\n  après : %s\n  phrase: %s",
            ligne["code_operation"],
            ligne["libelle"],
            json.dumps(avant, ensure_ascii=False),
            json.dumps(apres, ensure_ascii=False),
            phrase,
        )
        if expand(apres) != expand(avant) and avant.get("type") not in TYPES_SANS_ANNEES:
            log.warning(
                "Écart d'années sur %s : %s → %s",
                ligne["id"],
                expand(avant),
                expand(apres),
            )
        a_ecrire.append((ligne["id"], apres))
    if dry_run:
        log.info("dry-run : %s règle(s) affichée(s), aucune écriture.", len(a_ecrire))
        return len(a_ecrire)
    _ecrire_converties(a_ecrire)
    log.info("%s règle(s) écrite(s).", len(a_ecrire))
    return len(a_ecrire)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Règles de récurrence des échéances.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sous = parser.add_subparsers(dest="commande")

    p_expand = sous.add_parser("expand", help="Déroule une règle JSON en années.")
    p_expand.add_argument("regle", help="JSON de la récurrence")

    p_phrase = sous.add_parser("phrase", help="Phrase lisible d'une règle JSON.")
    p_phrase.add_argument("regle")
    p_phrase.add_argument("--fenetre-debut", default=None)
    p_phrase.add_argument("--fenetre-fin", default=None)
    p_phrase.add_argument("--traverse", action="store_true")
    p_phrase.add_argument("--annees", action="store_true", help="Ajoute les années calendaires.")

    p_conv = sous.add_parser("convertir", help="Convertit les échéances d'un projet, ou une règle JSON.")
    p_conv.add_argument("--projet", default=None)
    p_conv.add_argument("--json", dest="regle", default=None)
    p_conv.add_argument("--annee-n", type=int, default=None)
    p_conv.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    if args.commande == "expand":
        print(json.dumps(expand(json.loads(args.regle)), ensure_ascii=False))
        return
    if args.commande == "phrase":
        print(
            to_phrase(
                json.loads(args.regle),
                args.fenetre_debut,
                args.fenetre_fin,
                traverse_nouvel_an=args.traverse,
                avec_annees=args.annees,
            )
        )
        return
    if args.commande == "convertir":
        if args.regle:
            print(
                json.dumps(
                    convertir(json.loads(args.regle), args.annee_n),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if not args.projet:
            parser.error("convertir attend --projet ou --json")
        # Sans --dry-run la base est mise à jour.
        convertir_projet(args.projet, annee_n=args.annee_n, dry_run=bool(args.dry_run))
        return
    parser.print_help()


if __name__ == "__main__":
    # Permet `python recurrence.py` depuis n'importe quel cwd.
    _backend = str(__import__("pathlib").Path(__file__).resolve().parents[2])
    if _backend not in sys.path:
        sys.path.insert(0, _backend)
    try:
        main()
    except RecurrenceError as exc:
        sys.exit(str(exc))
