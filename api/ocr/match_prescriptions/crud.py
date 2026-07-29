"""CRUD couverture prescription ↔ échéance."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from api.db.env import get_database_url
from api.ocr.match_prescriptions.schemas import (
    AppariementEtatOut,
    CouvertureSyntheseOut,
    EcheanceMiniOut,
    LienCouvertureOut,
    PrescriptionMiniOut,
)


def _conn():
    return psycopg.connect(get_database_url(), row_factory=dict_row)


def _assert_projet_accessible(
    cur: Any,
    projet_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> None:
    if role in ("controleur", "admin"):
        cur.execute("SELECT 1 FROM bancarisation.projets WHERE id = %s", (str(projet_id),))
    elif organisation_id is None:
        raise PermissionError("Accès refusé")
    else:
        cur.execute(
            """
            SELECT 1 FROM bancarisation.projets
            WHERE id = %s AND organisation_id = %s
            """,
            (str(projet_id), str(organisation_id)),
        )
    if not cur.fetchone():
        raise LookupError("Projet introuvable")


def _assert_arrete_du_projet(cur: Any, projet_id: UUID, arrete_id: UUID) -> None:
    cur.execute(
        """
        SELECT 1 FROM bancarisation.arrete
        WHERE id = %s AND projet_id = %s
        """,
        (str(arrete_id), str(projet_id)),
    )
    if not cur.fetchone():
        raise LookupError("Arrêté introuvable pour ce projet")


def _parse_recurrence(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _map_prescription(r: dict[str, Any]) -> PrescriptionMiniOut:
    return PrescriptionMiniOut(
        id=r["id"],
        article=r.get("article"),
        intitule=r.get("intitule") or "",
        categorie=r.get("categorie"),
        nature=r.get("nature"),
        cible_valeur=float(r["cible_valeur"]) if r.get("cible_valeur") is not None else None,
        cible_unite=r.get("cible_unite"),
        echeance=r.get("echeance") if isinstance(r.get("echeance"), date) else (
            date.fromisoformat(str(r["echeance"])[:10]) if r.get("echeance") else None
        ),
        page_source=r.get("page_source"),
        texte_source=r.get("texte_source"),
    )


def _map_echeance(r: dict[str, Any]) -> EcheanceMiniOut:
    return EcheanceMiniOut(
        id=r["id"],
        code_operation=r.get("code_operation"),
        type_operation=r.get("type_operation"),
        type_metier=r.get("type_metier"),
        libelle=r.get("libelle"),
        objectif_operationnel=r.get("objectif_operationnel"),
        lib_thema=r.get("lib_thema"),
        recurrence=_parse_recurrence(r.get("recurrence")),
        action_cle=r.get("action_cle"),
    )


def lister_prescriptions_arrete(cur: Any, arrete_id: UUID) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, article, intitule, categorie, nature,
               cible_valeur, cible_unite, echeance, recurrence,
               page_source, texte_source, confiance
        FROM bancarisation.arrete_prescription
        WHERE arrete_id = %s
        ORDER BY article NULLS LAST, cree_le
        """,
        (str(arrete_id),),
    )
    return list(cur.fetchall())


def lister_echeances_projet(cur: Any, projet_id: UUID) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, code_operation, type_operation, type_metier, libelle,
               objectif_operationnel, lib_thema, recurrence, action_cle
        FROM bancarisation.echeance
        WHERE projet_id = %s
        ORDER BY code_operation NULLS LAST, libelle
        """,
        (str(projet_id),),
    )
    return list(cur.fetchall())


def lister_liens(
    cur: Any,
    *,
    projet_id: UUID,
    arrete_id: UUID | None = None,
) -> list[dict[str, Any]]:
    if arrete_id is not None:
        cur.execute(
            """
            SELECT c.prescription_id, c.echeance_id, c.mode, c.confiance, c.note, c.cree_le
            FROM bancarisation.prescription_couverture c
            JOIN bancarisation.arrete_prescription p ON p.id = c.prescription_id
            WHERE p.arrete_id = %s
            ORDER BY c.mode DESC, c.confiance DESC NULLS LAST, c.cree_le
            """,
            (str(arrete_id),),
        )
    else:
        cur.execute(
            """
            SELECT c.prescription_id, c.echeance_id, c.mode, c.confiance, c.note, c.cree_le
            FROM bancarisation.prescription_couverture c
            JOIN bancarisation.arrete_prescription p ON p.id = c.prescription_id
            JOIN bancarisation.arrete a ON a.id = p.arrete_id
            WHERE a.projet_id = %s
            ORDER BY c.mode DESC, c.confiance DESC NULLS LAST
            """,
            (str(projet_id),),
        )
    return list(cur.fetchall())


def lister_syntheses(cur: Any, projet_id: UUID, arrete_id: UUID) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT v.prescription_id, v.nb_echeances, v.nb_occurrences,
               v.nb_realisees, v.nb_en_retard, v.prochaine_echeance, v.couverture
        FROM bancarisation.v_prescription_couverture v
        JOIN bancarisation.arrete_prescription p ON p.id = v.prescription_id
        WHERE v.projet_id = %s AND p.arrete_id = %s
        """,
        (str(projet_id), str(arrete_id)),
    )
    return list(cur.fetchall())


def inserer_couvertures_ia(cur: Any, rows: list[dict[str, Any]]) -> int:
    """Insert propositions IA. Ne jamais écraser un lien user (PK + DO NOTHING)."""
    n = 0
    for row in rows:
        cur.execute(
            """
            INSERT INTO bancarisation.prescription_couverture
              (prescription_id, echeance_id, mode, confiance, note)
            VALUES (%s, %s, 'ia', %s, %s)
            ON CONFLICT (prescription_id, echeance_id) DO NOTHING
            """,
            (
                str(row["prescription_id"]),
                str(row["echeance_id"]),
                row.get("confiance"),
                row.get("note"),
            ),
        )
        n += cur.rowcount
    return n


def valider_lien(cur: Any, prescription_id: UUID, echeance_id: UUID) -> dict[str, Any]:
    """Bascule ia → user, ou crée un lien user s'il n'existait pas."""
    cur.execute(
        """
        INSERT INTO bancarisation.prescription_couverture
          (prescription_id, echeance_id, mode, confiance, note)
        VALUES (%s, %s, 'user', NULL, NULL)
        ON CONFLICT (prescription_id, echeance_id) DO UPDATE SET
          mode = 'user',
          note = coalesce(bancarisation.prescription_couverture.note, EXCLUDED.note)
        RETURNING prescription_id, echeance_id, mode, confiance, note, cree_le
        """,
        (str(prescription_id), str(echeance_id)),
    )
    r = cur.fetchone()
    if not r:
        raise LookupError("Impossible de valider le lien")
    return r


def rejeter_proposition(cur: Any, prescription_id: UUID, echeance_id: UUID) -> bool:
    """Supprime uniquement une proposition IA (pas un lien user)."""
    cur.execute(
        """
        DELETE FROM bancarisation.prescription_couverture
        WHERE prescription_id = %s AND echeance_id = %s AND mode = 'ia'
        """,
        (str(prescription_id), str(echeance_id)),
    )
    return cur.rowcount > 0


def supprimer_lien_user(cur: Any, prescription_id: UUID, echeance_id: UUID) -> bool:
    cur.execute(
        """
        DELETE FROM bancarisation.prescription_couverture
        WHERE prescription_id = %s AND echeance_id = %s AND mode = 'user'
        """,
        (str(prescription_id), str(echeance_id)),
    )
    return cur.rowcount > 0


def construire_etat(
    cur: Any,
    projet_id: UUID,
    arrete_id: UUID,
) -> AppariementEtatOut:
    presc_rows = lister_prescriptions_arrete(cur, arrete_id)
    ech_rows = lister_echeances_projet(cur, projet_id)
    liens_rows = lister_liens(cur, projet_id=projet_id, arrete_id=arrete_id)
    synth_rows = lister_syntheses(cur, projet_id, arrete_id)

    presc_by = {str(r["id"]): _map_prescription(r) for r in presc_rows}
    ech_by = {str(r["id"]): _map_echeance(r) for r in ech_rows}

    liens: list[LienCouvertureOut] = []
    nb_ia = 0
    nb_user = 0
    for r in liens_rows:
        mode = r["mode"]
        if mode == "ia":
            nb_ia += 1
        else:
            nb_user += 1
        pid, eid = str(r["prescription_id"]), str(r["echeance_id"])
        liens.append(
            LienCouvertureOut(
                prescription_id=r["prescription_id"],
                echeance_id=r["echeance_id"],
                mode=mode,
                confiance=float(r["confiance"]) if r.get("confiance") is not None else None,
                note=r.get("note"),
                cree_le=r.get("cree_le") if isinstance(r.get("cree_le"), datetime) else None,
                prescription=presc_by.get(pid),
                echeance=ech_by.get(eid),
            )
        )

    syntheses = [
        CouvertureSyntheseOut(
            prescription_id=s["prescription_id"],
            nb_echeances=int(s.get("nb_echeances") or 0),
            nb_occurrences=int(s.get("nb_occurrences") or 0),
            nb_realisees=int(s.get("nb_realisees") or 0),
            nb_en_retard=int(s.get("nb_en_retard") or 0),
            prochaine_echeance=s.get("prochaine_echeance"),
            couverture=s.get("couverture") or "non_couverte",
        )
        for s in synth_rows
    ]
    nb_non = sum(1 for s in syntheses if s.couverture == "non_couverte")
    # Si la vue n'a pas encore de ligne (0 liens), compter toutes les prescriptions
    if not syntheses and presc_rows:
        nb_non = len(presc_rows)
        syntheses = [
            CouvertureSyntheseOut(
                prescription_id=r["id"],
                couverture="non_couverte",
            )
            for r in presc_rows
        ]

    return AppariementEtatOut(
        projet_id=projet_id,
        arrete_id=arrete_id,
        prescriptions=[presc_by[str(r["id"])] for r in presc_rows],
        echeances=[ech_by[str(r["id"])] for r in ech_rows],
        liens=liens,
        syntheses=syntheses,
        nb_propositions_ia=nb_ia,
        nb_valides=nb_user,
        nb_non_couvertes=nb_non,
    )


def lire_etat(
    projet_id: UUID,
    arrete_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> AppariementEtatOut:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            _assert_arrete_du_projet(cur, projet_id, arrete_id)
            return construire_etat(cur, projet_id, arrete_id)


def charger_pour_match(
    projet_id: UUID,
    arrete_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            _assert_arrete_du_projet(cur, projet_id, arrete_id)
            presc = lister_prescriptions_arrete(cur, arrete_id)
            ech = lister_echeances_projet(cur, projet_id)
            # ids en str pour le LLM
            for p in presc:
                p["id"] = str(p["id"])
            for e in ech:
                e["id"] = str(e["id"])
            return presc, ech


def persister_propositions_ia(
    projet_id: UUID,
    arrete_id: UUID,
    rows: list[dict[str, Any]],
    *,
    role: str,
    organisation_id: UUID | None,
) -> tuple[int, AppariementEtatOut]:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            _assert_arrete_du_projet(cur, projet_id, arrete_id)
            n = inserer_couvertures_ia(cur, rows)
            conn.commit()
            return n, construire_etat(cur, projet_id, arrete_id)


def action_valider_lien(
    projet_id: UUID,
    prescription_id: UUID,
    echeance_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> AppariementEtatOut:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            # Vérifier que la prescription appartient au projet
            cur.execute(
                """
                SELECT p.arrete_id FROM bancarisation.arrete_prescription p
                JOIN bancarisation.arrete a ON a.id = p.arrete_id
                WHERE p.id = %s AND a.projet_id = %s
                """,
                (str(prescription_id), str(projet_id)),
            )
            row = cur.fetchone()
            if not row:
                raise LookupError("Prescription introuvable")
            arrete_id = row["arrete_id"]
            cur.execute(
                "SELECT 1 FROM bancarisation.echeance WHERE id = %s AND projet_id = %s",
                (str(echeance_id), str(projet_id)),
            )
            if not cur.fetchone():
                raise LookupError("Échéance introuvable")
            valider_lien(cur, prescription_id, echeance_id)
            conn.commit()
            return construire_etat(cur, projet_id, arrete_id)


def action_rejeter_proposition(
    projet_id: UUID,
    prescription_id: UUID,
    echeance_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> AppariementEtatOut:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            cur.execute(
                """
                SELECT p.arrete_id FROM bancarisation.arrete_prescription p
                JOIN bancarisation.arrete a ON a.id = p.arrete_id
                WHERE p.id = %s AND a.projet_id = %s
                """,
                (str(prescription_id), str(projet_id)),
            )
            row = cur.fetchone()
            if not row:
                raise LookupError("Prescription introuvable")
            arrete_id = row["arrete_id"]
            if not rejeter_proposition(cur, prescription_id, echeance_id):
                raise LookupError("Aucune proposition IA à rejeter")
            conn.commit()
            return construire_etat(cur, projet_id, arrete_id)


def action_retirer_validation(
    projet_id: UUID,
    prescription_id: UUID,
    echeance_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> AppariementEtatOut:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            cur.execute(
                """
                SELECT p.arrete_id FROM bancarisation.arrete_prescription p
                JOIN bancarisation.arrete a ON a.id = p.arrete_id
                WHERE p.id = %s AND a.projet_id = %s
                """,
                (str(prescription_id), str(projet_id)),
            )
            row = cur.fetchone()
            if not row:
                raise LookupError("Prescription introuvable")
            arrete_id = row["arrete_id"]
            if not supprimer_lien_user(cur, prescription_id, echeance_id):
                raise LookupError("Aucun lien validé à retirer")
            conn.commit()
            return construire_etat(cur, projet_id, arrete_id)
