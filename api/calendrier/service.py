"""Lecture et écriture des échéances-règles, puis régénération."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from api.calendrier.generation import _connexion, regenerer_occurrences, regenerer_projet
from api.calendrier.recurrence import (
    RecurrenceError,
    expand,
    hypothese_pas,
    to_phrase,
)
from api.ocr.domain.ug_ids import normalize_ug_ids

log = logging.getLogger("calendrier.service")

_CHAMPS = {
    "libelle", "code_operation", "type_operation", "lib_thema", "ug_ids",
    "recurrence", "fenetre_debut", "fenetre_fin", "fenetre_traverse_nouvel_an",
    "hypothese", "champs_a_confirmer",
}


class EcheanceIntrouvable(LookupError):
    pass


class EcheancesFigees(RuntimeError):
    def __init__(self, nombre: int) -> None:
        super().__init__(
            f"{nombre} occurrence(s) figée(s) : confirmer la suppression pour détacher la règle."
        )
        self.nombre = nombre


def _jsonable(valeur: Any) -> Any:
    if isinstance(valeur, UUID):
        return str(valeur)
    if isinstance(valeur, (datetime, date)):
        return valeur.isoformat()
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, dict):
        return {cle: _jsonable(v) for cle, v in valeur.items()}
    if isinstance(valeur, list):
        return [_jsonable(v) for v in valeur]
    return valeur


def preparer_recurrence(recurrence: dict[str, Any], annee_n: int | None = None) -> dict[str, Any]:
    """Normalise une règle éditée et recalcule le cache ``annees``."""
    from api.calendrier.recurrence import convertir

    brut = dict(recurrence or {})
    if annee_n is not None and brut.get("annee_N") is None:
        brut["annee_N"] = annee_n
    type_ = brut.get("type")
    ancien = type_ in ("ponctuel", "periodique", "campagnes") or (
        type_ == "explicite" and brut.get("annees") and not brut.get("annees_explicites")
    ) or (
        type_ == "paliers"
        and brut.get("paliers")
        and "intervalle_ans" in brut["paliers"][0]
    )
    if ancien:
        return convertir(brut, annee_n if annee_n is not None else brut.get("annee_N"))
    brut["annees"] = expand(brut)
    return brut


def _phrase_sure(ligne: dict[str, Any]) -> tuple[str, list[int], str | None]:
    recurrence = ligne.get("recurrence") or {}
    try:
        annees = expand(recurrence)
        phrase = to_phrase(
            recurrence,
            ligne.get("fenetre_debut"),
            ligne.get("fenetre_fin"),
            traverse_nouvel_an=bool(ligne.get("fenetre_traverse_nouvel_an")),
        )
        return phrase, annees, None
    except RecurrenceError as exc:
        return (
            (recurrence.get("regle_source") or "Récurrence à compléter"),
            [],
            str(exc),
        )


def programme(projet_id: UUID | str) -> list[dict[str, Any]]:
    with _connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.cle, e.code_operation, e.libelle, e.type_operation, e.lib_thema,
                       e.ug_ids, e.recurrence, e.fenetre_debut, e.fenetre_fin,
                       e.fenetre_traverse_nouvel_an, e.statut, e.origine, e.hypothese,
                       e.champs_a_confirmer, e.avertissements, e.source_page, e.source_extrait,
                       e.confiance,
                       count(o.id) FILTER (WHERE o.statut IS DISTINCT FROM 'supprime') AS nb_occurrences,
                       count(o.id) FILTER (
                           WHERE o.fige AND o.statut IS DISTINCT FROM 'supprime'
                       ) AS nb_figees
                FROM bancarisation.echeance e
                LEFT JOIN bancarisation.occurrence o ON o.echeance_id = e.id
                WHERE e.projet_id = %s
                GROUP BY e.id
                ORDER BY e.code_operation, e.libelle
                """,
                (str(projet_id),),
            )
            lignes = [dict(ligne) for ligne in cur.fetchall()]
    resultat = []
    for ligne in lignes:
        phrase, annees, erreur = _phrase_sure(ligne)
        if erreur:
            avertissements = list(ligne.get("avertissements") or [])
            if erreur not in avertissements:
                avertissements.append(erreur)
            ligne["avertissements"] = avertissements
        ligne["phrase"] = phrase
        ligne["annees"] = annees
        ligne["nb_occurrences"] = int(ligne.get("nb_occurrences") or 0)
        ligne["nb_figees"] = int(ligne.get("nb_figees") or 0)
        resultat.append(_jsonable(ligne))
    return resultat


def _reponse_apercu(regle: dict[str, Any], fenetre: dict[str, Any], nb_figees: int) -> dict[str, Any]:
    return {
        "phrase": to_phrase(
            regle,
            fenetre.get("fenetre_debut"),
            fenetre.get("fenetre_fin"),
            traverse_nouvel_an=bool(fenetre.get("fenetre_traverse_nouvel_an")),
        ),
        "annees": regle.get("annees") or [],
        "nb_occurrences": len(regle.get("annees") or []),
        "nb_figees": nb_figees,
        "recurrence": regle,
        "hypothese": hypothese_pas(regle),
    }


def apercu_brouillon(recurrence: dict[str, Any], fenetre: dict[str, Any]) -> dict[str, Any]:
    """Aperçu d'une règle pas encore enregistrée."""
    return _reponse_apercu(preparer_recurrence(recurrence), fenetre, 0)


def apercu(echeance_id: UUID | str, recurrence: dict[str, Any], fenetre: dict[str, Any]) -> dict[str, Any]:
    regle = preparer_recurrence(recurrence)
    with _connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FILTER (WHERE fige OR date_realisation IS NOT NULL) AS nb_figees
                FROM bancarisation.occurrence
                WHERE echeance_id = %s AND statut IS DISTINCT FROM 'supprime'
                """,
                (str(echeance_id),),
            )
            compte = cur.fetchone() or {"nb_figees": 0}
    return _reponse_apercu(regle, fenetre, int(compte["nb_figees"] or 0))


def _poser_audit(champs: dict[str, Any], acteur: str | None) -> None:
    champs["modifie_le"] = datetime.now().astimezone().isoformat()
    if acteur:
        try:
            champs["modifie_par"] = str(UUID(acteur))
        except ValueError:
            champs["modifie_par"] = None


def modifier_echeance(
    echeance_id: UUID | str,
    champs: dict[str, Any],
    *,
    acteur: str | None = None,
) -> dict[str, Any]:
    maj = {cle: valeur for cle, valeur in champs.items() if cle in _CHAMPS}
    if not maj:
        raise ValueError("Aucun champ modifiable.")
    if "ug_ids" in maj:
        maj["ug_ids"] = normalize_ug_ids(maj["ug_ids"])
    if "recurrence" in maj:
        maj["recurrence"] = preparer_recurrence(maj["recurrence"])
        if not maj.get("hypothese"):
            auto = hypothese_pas(maj["recurrence"])
            if auto:
                maj["hypothese"] = auto
    _poser_audit(maj, acteur)
    identifiant = str(echeance_id)
    with _connexion() as conn:
        with conn.cursor() as cur:
            morceaux = []
            valeurs: list[Any] = []
            for cle, valeur in maj.items():
                morceaux.append(f"{cle} = %s")
                valeurs.append(Jsonb(valeur) if cle == "recurrence" else valeur)
            valeurs.append(identifiant)
            cur.execute(
                f"""
                UPDATE bancarisation.echeance
                SET {", ".join(morceaux)}
                WHERE id = %s
                RETURNING id
                """,
                valeurs,
            )
            if cur.fetchone() is None:
                raise EcheanceIntrouvable(identifiant)
    rapport = regenerer_occurrences(identifiant)
    return {"echeance_id": identifiant, "rapport": rapport}


def valider_echeance(echeance_id: UUID | str, *, acteur: str | None = None) -> dict[str, Any]:
    identifiant = str(echeance_id)
    champs: dict[str, Any] = {"statut": "validee"}
    _poser_audit(champs, acteur)
    with _connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bancarisation.echeance
                SET statut = %s, modifie_le = %s, modifie_par = %s
                WHERE id = %s
                RETURNING id
                """,
                (champs["statut"], champs["modifie_le"], champs.get("modifie_par"), identifiant),
            )
            if cur.fetchone() is None:
                raise EcheanceIntrouvable(identifiant)
    rapport = regenerer_occurrences(identifiant)
    return {"echeance_id": identifiant, "statut": "validee", "rapport": rapport}


def creer_echeance(projet_id: UUID | str, champs: dict[str, Any], *, acteur: str | None = None) -> dict[str, Any]:
    libelle = (champs.get("libelle") or "").strip()
    code = (champs.get("code_operation") or "").strip()
    if not libelle or not code:
        raise ValueError("Le code et le libellé sont obligatoires.")
    recurrence = preparer_recurrence(champs.get("recurrence") or {
        "type": "explicite",
        "annee_N": champs.get("annee_N") or date.today().year,
        "annees_explicites": [0],
        "duree_ans": 30,
    })
    hypothese = champs.get("hypothese") or hypothese_pas(recurrence)
    ug_ids = normalize_ug_ids(champs.get("ug_ids") or [])
    identifiant = str(uuid4())
    cle = f"manuelle-{identifiant[:8]}"
    audit: dict[str, Any] = {}
    _poser_audit(audit, acteur)
    with _connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bancarisation.echeance (
                    id, projet_id, cle, code_operation, type_operation, type_metier,
                    libelle, lib_thema, ug_ids, recurrence, fenetre_debut, fenetre_fin,
                    fenetre_traverse_nouvel_an, confiance, statut, origine, hypothese,
                    champs_a_confirmer, modifie_le, modifie_par
                ) VALUES (
                    %s, %s, %s, %s, %s, 'autre',
                    %s, %s, %s, %s, %s, %s,
                    %s, 1, 'a_confirmer', 'manuelle', %s,
                    %s, %s, %s
                )
                RETURNING id
                """,
                (
                    identifiant, str(projet_id), cle, code,
                    champs.get("type_operation") or "MG",
                    libelle, champs.get("lib_thema") or "autre", ug_ids, Jsonb(recurrence),
                    champs.get("fenetre_debut"), champs.get("fenetre_fin"),
                    bool(champs.get("fenetre_traverse_nouvel_an")),
                    hypothese, champs.get("champs_a_confirmer") or [],
                    audit["modifie_le"], audit.get("modifie_par"),
                ),
            )
            if cur.fetchone() is None:
                raise RuntimeError("Création de l'échéance impossible.")
    rapport = regenerer_occurrences(identifiant)
    log.info("échéance manuelle %s créée (%s)", identifiant, code)
    return {"id": identifiant, "rapport": rapport}


def supprimer_echeance(echeance_id: UUID | str, *, forcer: bool = False) -> dict[str, Any]:
    identifiant = str(echeance_id)
    with _connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS n
                FROM bancarisation.occurrence
                WHERE echeance_id = %s AND (fige OR date_realisation IS NOT NULL)
                """,
                (identifiant,),
            )
            nombre = int((cur.fetchone() or {"n": 0})["n"])
            if nombre and not forcer:
                raise EcheancesFigees(nombre)
            cur.execute(
                """
                DELETE FROM bancarisation.occurrence
                WHERE echeance_id = %s AND fige = false AND date_realisation IS NULL
                """,
                (identifiant,),
            )
            cur.execute(
                "DELETE FROM bancarisation.echeance WHERE id = %s RETURNING id",
                (identifiant,),
            )
            if cur.fetchone() is None:
                raise EcheanceIntrouvable(identifiant)
    log.info("échéance %s supprimée (figées détachées : %s)", identifiant, nombre if forcer else 0)
    return {"supprimee": identifiant, "occurrences_figees_detachees": nombre if forcer else 0}


def regenerer_tout(projet_id: UUID | str, *, dry_run: bool = False) -> list[dict[str, Any]]:
    return regenerer_projet(projet_id, dry_run=dry_run)
