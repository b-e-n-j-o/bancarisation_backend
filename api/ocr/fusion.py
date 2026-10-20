"""Fusion de tâches (occurrences) : une nouvelle tâche, les sources sortent du calendrier.

Conservé : notes internes, documents, demandes DREAL, personnes en charge,
montants (additionnés sur la tâche d'ancrage).
Abandonné : étapes. Le statut de la nouvelle tâche est « planifiée ».
La surface n'est pas recopiée : elle se recalcule sur les unités choisies.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.budget.finance import ecrire_finance, lire_finance, monter_finance
from api.budget.mouvements import appliquer_contexte_mouvement
from api.db.utilisateur import connect_utilisateur
from api.ocr.db.responsables import lire_responsables, remplacer_responsables
from api.ocr.domain.ug_ids import normalize_ug_ids


def fusionner_occurrences(
    projet_id: UUID | str,
    *,
    occurrence_ids: list[str],
    portee: str,
    titre: str,
    code: str,
    categorie: str,
    ug_ids: list[str],
    annee: int,
    mois_debut: int,
    mois_fin: int,
    traverse_nouvel_an: bool,
    horizon_ans: int,
    description: str | None = None,
) -> dict[str, Any]:
    if portee not in ("selection", "series"):
        raise ValueError("La portée doit être « selection » ou « series ».")
    if len(occurrence_ids) < 2:
        raise ValueError("Sélectionnez au moins deux tâches.")
    titre = titre.strip()
    code = code.strip()
    if not titre or not code:
        raise ValueError("Le nom et le code sont requis.")
    if not 1 <= mois_debut <= 12 or not 1 <= mois_fin <= 12:
        raise ValueError("Mois hors calendrier.")
    horizon = max(1, min(int(horizon_ans), 50))
    pid = str(projet_id)
    ug = normalize_ug_ids(ug_ids)

    with connect_utilisateur(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            sources = _charger_sources(cur, pid, occurrence_ids, portee)
            if len(sources) < 2:
                raise ValueError("Au moins deux tâches actives sont nécessaires.")
            ids = [str(s["id"]) for s in sources]
            finances = _lire_finances(cur, ids)
            responsables = _responsables_unis(cur, ids)
            presta_id, presta_nom = _prestataire_unique(sources)

            echeance_id = None
            if horizon > 1:
                echeance_id = _creer_echeance(
                    cur,
                    pid,
                    code=code,
                    titre=titre,
                    categorie=categorie,
                    ug_ids=ug,
                    mois_debut=mois_debut,
                    mois_fin=mois_fin,
                    traverse=traverse_nouvel_an,
                    horizon=horizon,
                )

            libelles = ", ".join(f"{s['code']} ({s['annee']})" for s in sources)
            motif = f"Fusion de {libelles}"
            appliquer_contexte_mouvement(cur, motif=motif, modifie_par=None)

            texte = (description or "").strip()
            if not texte:
                texte = f"Issue de la fusion de {libelles}."
            ancre_id = _inserer_occurrence(
                cur,
                projet_id=pid,
                echeance_id=echeance_id,
                annee=annee,
                code=code,
                titre=titre,
                categorie=categorie,
                ug_ids=ug,
                mois_debut=mois_debut,
                mois_fin=mois_fin,
                traverse=traverse_nouvel_an,
                prestataire=presta_nom,
                prestataire_id=presta_id,
                commentaire=texte,
            )
            for decalage in range(1, horizon):
                _inserer_occurrence(
                    cur,
                    projet_id=pid,
                    echeance_id=echeance_id,
                    annee=annee + decalage,
                    code=code,
                    titre=titre,
                    categorie=categorie,
                    ug_ids=ug,
                    mois_debut=mois_debut,
                    mois_fin=mois_fin,
                    traverse=traverse_nouvel_an,
                    prestataire=presta_nom,
                    prestataire_id=presta_id,
                    commentaire=texte,
                )

            finance = _sommer_finances(finances)
            if finance:
                ecrire_finance(cur, ancre_id, finance)
            if responsables:
                remplacer_responsables(cur, ancre_id, responsables)

            _deplacer_contenu(cur, ids, ancre_id, pid, sources)
            cur.execute(
                """
                UPDATE bancarisation.occurrence
                SET statut = 'archive', modifie_le = now()
                WHERE id = ANY(%s::uuid[])
                """,
                (ids,),
            )
            cur.execute(
                "SELECT * FROM bancarisation.occurrence WHERE id = %s",
                (ancre_id,),
            )
            row = cur.fetchone()
            return monter_finance(dict(row) if row else {}, lire_finance(cur, ancre_id)) or {}


def _charger_sources(cur, projet_id: str, occurrence_ids: list[str], portee: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, projet_id, echeance_id, annee, code, titre, categorie,
               prestataire, prestataire_id, commentaire, statut
        FROM bancarisation.occurrence
        WHERE projet_id = %s
          AND statut not in ('supprime', 'archive')
          AND (
            id = ANY(%s::uuid[])
            OR (
              %s = 'series'
              AND echeance_id IS NOT NULL
              AND echeance_id IN (
                SELECT echeance_id
                FROM bancarisation.occurrence
                WHERE id = ANY(%s::uuid[])
                  AND echeance_id IS NOT NULL
              )
            )
          )
        ORDER BY annee, code
        """,
        (projet_id, occurrence_ids, portee, occurrence_ids),
    )
    rows = [dict(r) for r in cur.fetchall()]
    demandes = {str(i) for i in occurrence_ids}
    trouvees = {str(r["id"]) for r in rows}
    if portee == "selection" and not demandes <= trouvees:
        raise ValueError("Certaines tâches sont introuvables ou déjà retirées.")
    if not any(str(r["id"]) in demandes for r in rows):
        raise ValueError("Aucune tâche sélectionnée n'appartient à ce projet.")
    return rows


def _lire_finances(cur, ids: list[str]) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT montant_ht, montant_engage, montant_realise, montant_initial, annee_initiale
        FROM bancarisation.occurrence_finance
        WHERE occurrence_id = ANY(%s::uuid[])
        """,
        (ids,),
    )
    return [dict(r) for r in cur.fetchall()]


def _sommer_finances(lignes: list[dict[str, Any]]) -> dict[str, Any]:
    if not lignes:
        return {}
    finance: dict[str, Any] = {}
    for cle in ("montant_ht", "montant_engage", "montant_realise", "montant_initial"):
        total = _somme(ligne.get(cle) for ligne in lignes)
        if total is not None:
            finance[cle] = total
    annees = [l["annee_initiale"] for l in lignes if l.get("annee_initiale") is not None]
    if annees:
        finance["annee_initiale"] = min(annees)
    return finance


def _somme(vals) -> Decimal | None:
    nums = [v for v in vals if v is not None]
    if not nums:
        return None
    total = Decimal(0)
    for v in nums:
        total += Decimal(str(v))
    return total


def _prestataire_unique(sources: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    ids = {str(s["prestataire_id"]) for s in sources if s.get("prestataire_id")}
    if len(ids) != 1:
        return None, None
    pid = next(iter(ids))
    nom = next(
        (s.get("prestataire") for s in sources if str(s.get("prestataire_id") or "") == pid and s.get("prestataire")),
        None,
    )
    return pid, nom


def _responsables_unis(cur, ids: list[str]) -> list[str]:
    par_occ = lire_responsables(cur, ids)
    vus: list[str] = []
    for oid in ids:
        for personne in par_occ.get(oid, []):
            pid = str(personne.get("personne_id") or "")
            if pid and pid not in vus:
                vus.append(pid)
    return vus


def _creer_echeance(
    cur,
    projet_id: str,
    *,
    code: str,
    titre: str,
    categorie: str,
    ug_ids: list[str],
    mois_debut: int,
    mois_fin: int,
    traverse: bool,
    horizon: int,
) -> str:
    cle = f"fusion-{uuid4()}"
    recurrence = {
        "type": "periodique",
        "intervalle_ans": 1,
        "duree_ans": horizon,
        "regle_source": "Fusion manuelle",
    }
    cur.execute(
        """
        INSERT INTO bancarisation.echeance (
          projet_id, cle, code_operation, type_operation, type_metier, libelle,
          ug_ids, recurrence, fenetre_debut, fenetre_fin,
          fenetre_traverse_nouvel_an, confiance
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            projet_id,
            cle,
            code,
            categorie,
            "fusion",
            titre,
            ug_ids,
            Jsonb(recurrence),
            str(mois_debut),
            str(mois_fin),
            traverse,
            1,
        ),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Création de l'échéance de fusion impossible.")
    return str(row["id"])


def _inserer_occurrence(
    cur,
    *,
    projet_id: str,
    echeance_id: str | None,
    annee: int,
    code: str,
    titre: str,
    categorie: str,
    ug_ids: list[str],
    mois_debut: int,
    mois_fin: int,
    traverse: bool,
    prestataire: str | None,
    prestataire_id: str | None,
    commentaire: str | None,
) -> str:
    cur.execute(
        """
        INSERT INTO bancarisation.occurrence (
          projet_id, echeance_id, annee, code, titre, categorie, statut,
          ug_ids, mois_debut, mois_fin, traverse_nouvel_an, origine,
          prestataire, prestataire_id, commentaire
        ) VALUES (
          %s, %s, %s, %s, %s, %s, 'planifie',
          %s, %s, %s, %s, 'user',
          %s, %s, %s
        )
        RETURNING id
        """,
        (
            projet_id,
            echeance_id,
            annee,
            code,
            titre,
            categorie,
            ug_ids,
            mois_debut,
            mois_fin,
            traverse,
            prestataire,
            prestataire_id,
            commentaire,
        ),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Création de la tâche fusionnée impossible.")
    return str(row["id"])


def _deplacer_contenu(cur, ids: list[str], ancre_id: str, projet_id: str, sources: list[dict[str, Any]]) -> None:
    cur.execute(
        """
        UPDATE bancarisation.note_interne
        SET occurrence_id = %s, etape_id = NULL
        WHERE occurrence_id = ANY(%s::uuid[])
        """,
        (ancre_id, ids),
    )
    for src in sources:
        texte = (src.get("commentaire") or "").strip()
        if not texte:
            continue
        cur.execute(
            """
            INSERT INTO bancarisation.note_interne (projet_id, occurrence_id, contenu)
            VALUES (%s, %s, %s)
            """,
            (projet_id, ancre_id, f"Commentaire de {src['code']} ({src['annee']}) : {texte}"),
        )
    presta_noms = sorted({
        str(s["prestataire"]).strip()
        for s in sources
        if s.get("prestataire") and str(s["prestataire"]).strip() not in ("", "—")
    })
    if len(presta_noms) > 1:
        cur.execute(
            """
            INSERT INTO bancarisation.note_interne (projet_id, occurrence_id, contenu)
            VALUES (%s, %s, %s)
            """,
            (
                projet_id,
                ancre_id,
                "Prestataires des tâches fusionnées : " + ", ".join(presta_noms) + ".",
            ),
        )
    cur.execute(
        """
        UPDATE bancarisation.documents
        SET occurrence_id = %s
        WHERE occurrence_id = ANY(%s::uuid[])
        """,
        (ancre_id, ids),
    )
    cur.execute(
        """
        UPDATE bancarisation.demande
        SET occurrence_id = %s
        WHERE occurrence_id = ANY(%s::uuid[])
        """,
        (ancre_id, ids),
    )
    cur.execute(
        "DELETE FROM bancarisation.action_etape WHERE occurrence_id = ANY(%s::uuid[])",
        (ids,),
    )
