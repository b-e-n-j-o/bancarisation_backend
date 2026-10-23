"""Service prestataires : référentiel global + rattachement projet."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api.db.utilisateur import connect_utilisateur

from .naf import libelle_naf


def _avec_libelle_naf(row: dict[str, Any]) -> dict[str, Any]:
    row["libelle_naf"] = libelle_naf(row.get("code_naf"))
    return row


def normaliser_siret(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits or None


def _connect():
    return connect_utilisateur(row_factory=dict_row)


def lister_global(*, q: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Annuaire de l'organisation : prestataires déjà liés à l'un de ses projets.

    Pas le référentiel mondial, pour pouvoir les reprendre sur un autre dossier.
    """
    terme = (q or "").strip()
    like = f"%{terme}%"
    siret_like = f"%{normaliser_siret(terme) or terme}%"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, nom, siret, telephone, email, commune, actif
                FROM bancarisation.prestataires p
                WHERE actif IS TRUE
                  AND p.id IN (
                    SELECT pp.prestataire_id
                    FROM bancarisation.projet_prestataire pp
                    JOIN bancarisation.projets pr ON pr.id = pp.projet_id
                    WHERE pr.organisation_id IN (
                      SELECT organisation_id FROM prive.mes_appartenances()
                    )
                    UNION
                    SELECT o.prestataire_id
                    FROM bancarisation.occurrence o
                    JOIN bancarisation.projets pr ON pr.id = o.projet_id
                    WHERE o.prestataire_id IS NOT NULL
                      AND pr.organisation_id IN (
                        SELECT organisation_id FROM prive.mes_appartenances()
                      )
                  )
                  AND (
                    %s = ''
                    OR p.nom ILIKE %s
                    OR coalesce(p.siret, '') ILIKE %s
                    OR coalesce(p.siret_norm, '') LIKE %s
                  )
                ORDER BY
                  CASE WHEN %s <> '' AND lower(p.nom) LIKE lower(%s) THEN 0 ELSE 1 END,
                  p.nom ASC
                LIMIT %s
                """,
                (
                    terme,
                    like,
                    like,
                    siret_like,
                    terme,
                    f"{terme}%",
                    limit,
                ),
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
                  categories_mesure, actif, notes, code_naf, tva
                FROM bancarisation.prestataires
                WHERE id = %s
                """,
                (str(prestataire_id),),
            )
            row = cur.fetchone()
            return _avec_libelle_naf(dict(row)) if row else None


def lister_projet(
    projet_id: UUID,
    *,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """Liste du projet seul.

    La vue `v_projet_prestataires` agrège d'abord toutes les occurrences
    de tous les projets, puis filtre. Ici le projet est filtré avant les sommes.
    """
    terme = (q or "").strip()
    like = f"%{terme}%"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH lies AS (
                  SELECT prestataire_id, role, created_at AS rattache_le
                  FROM bancarisation.projet_prestataire
                  WHERE projet_id = %s
                ),
                occ AS (
                  SELECT
                    o.prestataire_id,
                    count(*) FILTER (WHERE o.statut <> 'supprime') AS nb_occurrences,
                    count(*) FILTER (WHERE o.statut = 'realise') AS nb_realisees,
                    sum(f.montant_ht) FILTER (WHERE o.statut <> 'supprime') AS total_prevu_ht,
                    sum(f.montant_engage) FILTER (WHERE o.statut <> 'supprime') AS total_engage_ht,
                    sum(f.montant_realise) FILTER (WHERE o.statut <> 'supprime') AS total_realise_ht,
                    min(o.annee) FILTER (WHERE o.statut <> 'supprime') AS annee_min,
                    max(o.annee) FILTER (WHERE o.statut <> 'supprime') AS annee_max
                  FROM bancarisation.occurrence o
                  LEFT JOIN bancarisation.occurrence_finance f ON f.occurrence_id = o.id
                  WHERE o.projet_id = %s
                    AND o.prestataire_id IS NOT NULL
                  GROUP BY o.prestataire_id
                )
                SELECT
                  %s::text AS projet_id,
                  coalesce(occ.prestataire_id, lies.prestataire_id)::text AS id,
                  p.nom,
                  p.siret, p.siret_norm, p.forme_juridique,
                  p.adresse, p.code_postal, p.commune, p.departement,
                  p.email, p.telephone, p.interlocuteur,
                  p.specialites, p.categories_mesure, p.actif,
                  lies.role, lies.rattache_le::text,
                  CASE
                    WHEN occ.prestataire_id IS NOT NULL AND lies.prestataire_id IS NOT NULL THEN 'les_deux'
                    WHEN occ.prestataire_id IS NOT NULL THEN 'occurrence'
                    ELSE 'projet'
                  END AS source,
                  coalesce(occ.nb_occurrences, 0) AS nb_occurrences,
                  coalesce(occ.nb_realisees, 0) AS nb_realisees,
                  occ.total_prevu_ht::float,
                  occ.total_engage_ht::float,
                  occ.total_realise_ht::float,
                  occ.annee_min, occ.annee_max,
                  p.code_naf, p.tva
                FROM occ
                FULL JOIN lies ON lies.prestataire_id = occ.prestataire_id
                JOIN bancarisation.prestataires p
                  ON p.id = coalesce(occ.prestataire_id, lies.prestataire_id)
                WHERE %s = ''
                   OR p.nom ILIKE %s
                   OR coalesce(p.siret, '') ILIKE %s
                ORDER BY p.nom ASC
                """,
                (str(projet_id), str(projet_id), str(projet_id), terme, like, like),
            )
            return [_avec_libelle_naf(dict(r)) for r in cur.fetchall()]


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
    departement: str | None = None,
    code_naf: str | None = None,
    tva: str | None = None,
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
                      nom, siret, adresse, code_postal, commune, departement,
                      code_naf, tva, telephone, email, interlocuteur
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id::text, nom
                    """,
                    (
                        nom_clean,
                        siret,
                        adresse,
                        code_postal,
                        commune,
                        departement,
                        code_naf,
                        tva,
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
                      departement = COALESCE(NULLIF(%s, ''), departement),
                      code_naf = COALESCE(NULLIF(%s, ''), code_naf),
                      tva = COALESCE(NULLIF(%s, ''), tva),
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
                        departement,
                        code_naf,
                        tva,
                        telephone,
                        email,
                        interlocuteur,
                        pid,
                    ),
                )

            upsert_lien_projet(cur, projet_id, pid, role=role)

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
                UPDATE bancarisation.occurrence
                SET prestataire_id = NULL,
                    prestataire = NULL
                WHERE projet_id = %s
                  AND statut <> 'supprime'
                  AND (
                    prestataire_id = %s
                    OR (
                      prestataire_id IS NULL
                      AND lower(trim(coalesce(prestataire, ''))) = (
                        SELECT lower(trim(nom))
                        FROM bancarisation.prestataires
                        WHERE id = %s
                      )
                    )
                  )
                """,
                (str(projet_id), str(prestataire_id), str(prestataire_id)),
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
        "code_naf",
        "tva",
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
