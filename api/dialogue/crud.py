"""Canal de dialogue DREAL ↔ bureau d'études — accès aux données.

Emplacement : backend/api/dialogue/crud.py

V0 : pas de rôles. L'acteur ('dreal' | 'be') est transmis par le front
selon la section d'où l'on écrit. Le statut de la demande est tenu par le
trigger de la migration 017, pas ici.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_database_url


class DialogueError(Exception):
    pass


ACTEURS = ("dreal", "be")


def _verifier_acteur(acteur: str) -> str:
    if acteur not in ACTEURS:
        raise DialogueError(f"Acteur inconnu : {acteur!r} (attendu : dreal | be).")
    return acteur


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

_SELECT_DEMANDE = """
    d.id::text,
    d.projet_id::text,
    d.occurrence_id::text,
    d.annee,
    d.signal_code,
    d.objet,
    d.statut,
    d.auteur,
    d.cree_le::text,
    d.maj_le::text,
    d.clos_le::text
"""


def lister_demandes(projet_id: UUID) -> list[dict[str, Any]]:
    """Demandes d'un projet, avec un aperçu du dernier message."""
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    {_SELECT_DEMANDE},
                    o.code  AS occurrence_code,
                    o.titre AS occurrence_titre,
                    o.annee AS occurrence_annee,
                    COALESCE(o.ug_ids, '{{}}'::text[]) AS occurrence_ug_ids,
                    (SELECT count(*) FROM bancarisation.demande_message m
                     WHERE m.demande_id = d.id) AS nb_messages,
                    (SELECT m.corps FROM bancarisation.demande_message m
                     WHERE m.demande_id = d.id
                     ORDER BY m.cree_le DESC LIMIT 1) AS dernier_corps,
                    (SELECT m.acteur FROM bancarisation.demande_message m
                     WHERE m.demande_id = d.id
                     ORDER BY m.cree_le DESC LIMIT 1) AS dernier_acteur,
                    (SELECT m.cree_le::text FROM bancarisation.demande_message m
                     WHERE m.demande_id = d.id
                     ORDER BY m.cree_le DESC LIMIT 1) AS dernier_message_le
                FROM bancarisation.demande d
                LEFT JOIN bancarisation.occurrence o ON o.id = d.occurrence_id
                WHERE d.projet_id = %s
                ORDER BY
                    (d.statut = 'close') ASC,   -- les closes en bas
                    d.maj_le DESC
                """,
                (str(projet_id),),
            )
            return [dict(r) for r in cur.fetchall()]


def lister_messages(demande_id: UUID) -> list[dict[str, Any]]:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id::text,
                    demande_id::text,
                    acteur,
                    auteur_nom,
                    corps,
                    ARRAY(SELECT unnest(documents_ids)::text) AS documents_ids,
                    origine,
                    cree_le::text
                FROM bancarisation.demande_message
                WHERE demande_id = %s
                ORDER BY cree_le ASC
                """,
                (str(demande_id),),
            )
            return [dict(r) for r in cur.fetchall()]


def lire_demande(demande_id: UUID) -> dict[str, Any]:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SELECT_DEMANDE},
                       p.nom AS projet_nom,
                       o.code  AS occurrence_code,
                       o.titre AS occurrence_titre,
                       o.annee AS occurrence_annee,
                       COALESCE(o.ug_ids, '{{}}'::text[]) AS occurrence_ug_ids
                FROM bancarisation.demande d
                JOIN bancarisation.projets p ON p.id = d.projet_id
                LEFT JOIN bancarisation.occurrence o ON o.id = d.occurrence_id
                WHERE d.id = %s
                """,
                (str(demande_id),),
            )
            row = cur.fetchone()
    if row is None:
        raise DialogueError("Demande introuvable.")
    demande = dict(row)
    demande["messages"] = lister_messages(demande_id)
    return demande


def lister_demandes_parc(
    statut: str | None = None,
    limite: int = 200,
) -> list[dict[str, Any]]:
    """Toutes les demandes du parc — vue transversale côté suivi DREAL."""
    clause = "TRUE" if statut in (None, "toutes") else "d.statut = %s"
    params: list[Any] = [] if statut in (None, "toutes") else [statut]
    params.append(limite)

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    {_SELECT_DEMANDE},
                    p.nom  AS projet_nom,
                    org.id::text AS organisation_id,
                    org.nom AS organisation_nom,
                    o.code  AS occurrence_code,
                    o.titre AS occurrence_titre,
                    o.annee AS occurrence_annee,
                    COALESCE(o.ug_ids, '{{}}'::text[]) AS occurrence_ug_ids,
                    (SELECT count(*) FROM bancarisation.demande_message m
                     WHERE m.demande_id = d.id) AS nb_messages,
                    (SELECT m.cree_le::text FROM bancarisation.demande_message m
                     WHERE m.demande_id = d.id
                     ORDER BY m.cree_le DESC LIMIT 1) AS dernier_message_le,
                    -- Ancienneté : ce qui doit remonter en tête d'une file de relance.
                    floor(EXTRACT(EPOCH FROM (now() - d.maj_le)) / 86400)::int AS jours_sans_reponse
                FROM bancarisation.demande d
                JOIN bancarisation.projets p ON p.id = d.projet_id
                JOIN bancarisation.organisations org ON org.id = p.organisation_id
                LEFT JOIN bancarisation.occurrence o ON o.id = d.occurrence_id
                WHERE {clause}
                ORDER BY
                    (d.statut = 'ouverte') DESC,
                    d.maj_le ASC              -- les plus anciennes sans réponse d'abord
                LIMIT %s
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]


def lister_activite(projet_id: UUID, limite: int = 200) -> list[dict[str, Any]]:
    """Fil conducteur du projet — vue dérivée, aucun journal stocké."""
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    survenu_le::text,
                    type,
                    acteur,
                    libelle,
                    demande_id::text
                FROM bancarisation.v_projet_activite
                WHERE projet_id = %s
                ORDER BY survenu_le DESC
                LIMIT %s
                """,
                (str(projet_id), limite),
            )
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------

def creer_demande(
    projet_id: UUID,
    objet: str,
    corps: str,
    acteur: str = "dreal",
    *,
    auteur_nom: str | None = None,
    occurrence_id: UUID | None = None,
    annee: int | None = None,
    signal_code: str | None = None,
    documents_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Crée la demande ET son premier message, dans une seule transaction.

    Une demande sans message n'aurait aucun sens : l'objet est le titre,
    le corps est la première prise de parole.
    """
    _verifier_acteur(acteur)
    if not objet.strip():
        raise DialogueError("L'objet de la demande est obligatoire.")
    if not corps.strip():
        raise DialogueError("Le message ne peut pas être vide.")

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bancarisation.demande
                    (projet_id, occurrence_id, annee, signal_code, objet, auteur)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (
                    str(projet_id),
                    str(occurrence_id) if occurrence_id else None,
                    annee,
                    signal_code,
                    objet.strip(),
                    acteur,
                ),
            )
            demande_id = cur.fetchone()["id"]

            cur.execute(
                """
                INSERT INTO bancarisation.demande_message
                    (demande_id, acteur, auteur_nom, corps, documents_ids)
                VALUES (%s, %s, %s, %s, %s::uuid[])
                """,
                (
                    demande_id,
                    acteur,
                    auteur_nom,
                    corps.strip(),
                    documents_ids or [],
                ),
            )
    return lire_demande(UUID(demande_id))


def ajouter_message(
    demande_id: UUID,
    acteur: str,
    corps: str,
    *,
    auteur_nom: str | None = None,
    documents_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Ajoute un message. Le trigger 017 met le statut à jour tout seul."""
    _verifier_acteur(acteur)
    if not corps.strip():
        raise DialogueError("Le message ne peut pas être vide.")

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM bancarisation.demande WHERE id = %s",
                (str(demande_id),),
            )
            if cur.fetchone() is None:
                raise DialogueError("Demande introuvable.")

            cur.execute(
                """
                INSERT INTO bancarisation.demande_message
                    (demande_id, acteur, auteur_nom, corps, documents_ids)
                VALUES (%s, %s, %s, %s, %s::uuid[])
                """,
                (
                    str(demande_id),
                    acteur,
                    auteur_nom,
                    corps.strip(),
                    documents_ids or [],
                ),
            )
    return lire_demande(demande_id)


def changer_statut_demande(demande_id: UUID, statut: str) -> dict[str, Any]:
    """Clôture ou réouverture explicite (le reste est automatique)."""
    if statut not in ("ouverte", "repondue", "close"):
        raise DialogueError(f"Statut inconnu : {statut!r}.")

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bancarisation.demande
                SET statut = %s,
                    maj_le = now(),
                    clos_le = CASE WHEN %s = 'close' THEN now() ELSE NULL END
                WHERE id = %s
                RETURNING id::text
                """,
                (statut, statut, str(demande_id)),
            )
            if cur.fetchone() is None:
                raise DialogueError("Demande introuvable.")
    return lire_demande(demande_id)
