"""Service prestataires : référentiel global + rattachement projet."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api.db.env import get_database_url


def normaliser_siret(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits or None


def _connect():
    return psycopg.connect(get_database_url(), row_factory=dict_row)


def lister_global(*, q: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    terme = (q or "").strip()
    with _connect() as conn:
        with conn.cursor() as cur:
            if terme:
                cur.execute(
                    """
                    SELECT id::text, nom, siret, telephone, email, commune, actif
                    FROM bancarisation.prestataires
                    WHERE actif IS TRUE
                      AND (
                        nom ILIKE %s
                        OR coalesce(siret, '') ILIKE %s
                        OR coalesce(siret_norm, '') LIKE %s
                      )
                    ORDER BY
                      CASE WHEN lower(nom) LIKE lower(%s) THEN 0 ELSE 1 END,
                      nom ASC
                    LIMIT %s
                    """,
                    (
                        f"%{terme}%",
                        f"%{terme}%",
                        f"%{normaliser_siret(terme) or terme}%",
                        f"{terme}%",
                        limit,
                    ),
                )
            else:
                cur.execute(
                    """
                    SELECT id::text, nom, siret, telephone, email, commune, actif
                    FROM bancarisation.prestataires
                    WHERE actif IS TRUE
                    ORDER BY nom ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
            return [dict(r) for r in cur.fetchall()]


def get_prestataire(prestataire_id: UUID) -> dict[str, Any] | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  id::text, nom, siret, siret_norm, forme_juridique,
                  adresse, code_postal, commune, departement,
                  email, telephone, interlocuteur, specialites,
                  categories_mesure, actif, notes
                FROM bancarisation.prestataires
                WHERE id = %s
                """,
                (str(prestataire_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def lister_projet(
    projet_id: UUID,
    *,
    q: str | None = None,
) -> list[dict[str, Any]]:
    terme = (q or "").strip()
    with _connect() as conn:
        with conn.cursor() as cur:
            if terme:
                cur.execute(
                    """
                    SELECT
                      v.projet_id::text,
                      v.prestataire_id::text AS id,
                      v.prestataire_nom AS nom,
                      v.siret, v.siret_norm, v.forme_juridique,
                      v.adresse, v.code_postal, v.commune, v.departement,
                      v.email, v.telephone, v.interlocuteur,
                      v.specialites, v.categories_mesure, v.actif,
                      v.role, v.rattache_le::text, v.source,
                      v.nb_occurrences, v.nb_realisees,
                      v.total_prevu_ht::float, v.total_engage_ht::float,
                      v.total_realise_ht::float,
                      v.annee_min, v.annee_max
                    FROM bancarisation.v_projet_prestataires v
                    WHERE v.projet_id = %s
                      AND (
                        v.prestataire_nom ILIKE %s
                        OR coalesce(v.siret, '') ILIKE %s
                      )
                    ORDER BY v.prestataire_nom ASC
                    """,
                    (str(projet_id), f"%{terme}%", f"%{terme}%"),
                )
            else:
                cur.execute(
                    """
                    SELECT
                      v.projet_id::text,
                      v.prestataire_id::text AS id,
                      v.prestataire_nom AS nom,
                      v.siret, v.siret_norm, v.forme_juridique,
                      v.adresse, v.code_postal, v.commune, v.departement,
                      v.email, v.telephone, v.interlocuteur,
                      v.specialites, v.categories_mesure, v.actif,
                      v.role, v.rattache_le::text, v.source,
                      v.nb_occurrences, v.nb_realisees,
                      v.total_prevu_ht::float, v.total_engage_ht::float,
                      v.total_realise_ht::float,
                      v.annee_min, v.annee_max
                    FROM bancarisation.v_projet_prestataires v
                    WHERE v.projet_id = %s
                    ORDER BY v.prestataire_nom ASC
                    """,
                    (str(projet_id),),
                )
            return [dict(r) for r in cur.fetchall()]


def trouver_par_siret(siret_norm: str) -> dict[str, Any] | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, nom, siret
                FROM bancarisation.prestataires
                WHERE siret_norm = %s
                LIMIT 1
                """,
                (siret_norm,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def upsert_lien_projet(
    cur: psycopg.Cursor,
    projet_id: UUID | str,
    prestataire_id: UUID | str,
    *,
    role: str | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO bancarisation.projet_prestataire (projet_id, prestataire_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (projet_id, prestataire_id) DO NOTHING
        """,
        (str(projet_id), str(prestataire_id), role),
    )


def rattacher(
    projet_id: UUID,
    *,
    prestataire_id: UUID | None = None,
    nom: str | None = None,
    siret: str | None = None,
    adresse: str | None = None,
    code_postal: str | None = None,
    commune: str | None = None,
    telephone: str | None = None,
    email: str | None = None,
    interlocuteur: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Attach existant ou create+attach (dédup SIRET)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM bancarisation.projets WHERE id = %s",
                (str(projet_id),),
            )
            if cur.fetchone() is None:
                raise LookupError("Projet introuvable.")

            pid: str | None = str(prestataire_id) if prestataire_id else None
            siret_norm = normaliser_siret(siret)

            if pid is None and siret_norm:
                cur.execute(
                    """
                    SELECT id::text FROM bancarisation.prestataires
                    WHERE siret_norm = %s LIMIT 1
                    """,
                    (siret_norm,),
                )
                found = cur.fetchone()
                if found:
                    pid = found["id"]

            if pid is None:
                nom_clean = (nom or "").strip()
                if not nom_clean:
                    raise ValueError("Nom ou prestataire_id requis.")
                cur.execute(
                    """
                    INSERT INTO bancarisation.prestataires (
                      nom, siret, adresse, code_postal, commune,
                      telephone, email, interlocuteur
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id::text, nom
                    """,
                    (
                        nom_clean,
                        siret,
                        adresse,
                        code_postal,
                        commune,
                        telephone,
                        email,
                        interlocuteur,
                    ),
                )
                created = dict(cur.fetchone())
                pid = created["id"]
            else:
                # Enrichit les champs vides si fournis
                cur.execute(
                    """
                    UPDATE bancarisation.prestataires SET
                      siret = COALESCE(NULLIF(%s, ''), siret),
                      adresse = COALESCE(NULLIF(%s, ''), adresse),
                      code_postal = COALESCE(NULLIF(%s, ''), code_postal),
                      commune = COALESCE(NULLIF(%s, ''), commune),
                      telephone = COALESCE(NULLIF(%s, ''), telephone),
                      email = COALESCE(NULLIF(%s, ''), email),
                      interlocuteur = COALESCE(NULLIF(%s, ''), interlocuteur)
                    WHERE id = %s
                    """,
                    (
                        siret,
                        adresse,
                        code_postal,
                        commune,
                        telephone,
                        email,
                        interlocuteur,
                        pid,
                    ),
                )

            upsert_lien_projet(cur, projet_id, pid, role=role)

        conn.commit()

    rows = lister_projet(projet_id)
    for r in rows:
        if r["id"] == pid:
            return r
    # Fallback minimal
    p = get_prestataire(UUID(pid))
    return {
        "id": pid,
        "nom": p["nom"] if p else nom,
        "projet_id": str(projet_id),
        "nb_occurrences": 0,
        "total_prevu_ht": 0,
    }


def detacher(projet_id: UUID, prestataire_id: UUID) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)::int AS n
                FROM bancarisation.occurrence
                WHERE projet_id = %s
                  AND prestataire_id = %s
                  AND statut <> 'supprime'
                """,
                (str(projet_id), str(prestataire_id)),
            )
            n = cur.fetchone()["n"]
            if n > 0:
                raise PermissionError(
                    f"Impossible de retirer : {n} occurrence(s) active(s) "
                    "référencent encore ce prestataire."
                )
            cur.execute(
                """
                DELETE FROM bancarisation.projet_prestataire
                WHERE projet_id = %s AND prestataire_id = %s
                RETURNING projet_id
                """,
                (str(projet_id), str(prestataire_id)),
            )
            if cur.fetchone() is None:
                raise LookupError("Rattachement introuvable.")
        conn.commit()


def patch_prestataire(
    prestataire_id: UUID,
    champs: dict[str, Any],
) -> dict[str, Any] | None:
    allowed = {
        "nom",
        "siret",
        "forme_juridique",
        "adresse",
        "code_postal",
        "commune",
        "departement",
        "email",
        "telephone",
        "interlocuteur",
        "notes",
        "actif",
    }
    maj = {k: v for k, v in champs.items() if k in allowed}
    if not maj:
        raise ValueError("Aucun champ modifiable.")
    sets = ", ".join(f"{k} = %s" for k in maj)
    values = list(maj.values()) + [str(prestataire_id)]
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE bancarisation.prestataires
                SET {sets}
                WHERE id = %s
                RETURNING id::text
                """,
                values,
            )
            if cur.fetchone() is None:
                return None
        conn.commit()
    return get_prestataire(prestataire_id)


def lier_occurrence_au_projet(
    occurrence_id: UUID,
    prestataire_id: UUID,
) -> None:
    """Après PATCH occurrence : assure le lien projet_prestataire."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT projet_id::text
                FROM bancarisation.occurrence
                WHERE id = %s
                """,
                (str(occurrence_id),),
            )
            row = cur.fetchone()
            if not row:
                return
            upsert_lien_projet(cur, row["projet_id"], prestataire_id)
        conn.commit()
