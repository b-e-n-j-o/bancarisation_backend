"""Bilan financier annuel : construction, génération, validation.

Cycle : POST génère un BROUILLON (snapshot jsonb + contrôles), le user
relit, puis POST /valider fige. Un bilan validé est immuable (trigger 013) ;
toute correction passe par une nouvelle version.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.db.env import get_database_url
from api.journal_actions import journaliser


class BilanError(Exception):
    pass


# ---------------------------------------------------------------------------
# Construction du snapshot
# ---------------------------------------------------------------------------

def _synthese(cur: psycopg.Cursor, projet_id: str, annee: int) -> dict[str, Any]:
    cur.execute(
        """
        SELECT initial::float8, prevu::float8, engage::float8,
               realise::float8, delta_prevu_initial::float8
        FROM bancarisation.v_budget_delta_annuel
        WHERE projet_id = %s AND annee = %s
        """,
        (projet_id, annee),
    )
    row = cur.fetchone() or {
        "initial": 0.0, "prevu": 0.0, "engage": 0.0,
        "realise": 0.0, "delta_prevu_initial": 0.0,
    }
    s = dict(row)
    s["taux_execution"] = round(s["realise"] / s["prevu"], 4) if s["prevu"] else None
    s["taux_engagement"] = round(s["engage"] / s["prevu"], 4) if s["prevu"] else None
    return s


def _lignes_annee(cur: psycopg.Cursor, projet_id: str, annee: int) -> list[dict[str, Any]]:
    """Occurrences de l'année, avec dernier motif tracé par champ montant."""
    cur.execute(
        """
        SELECT
            o.id::text,
            o.code,
            o.titre,
            o.categorie,
            o.lib_thema,
            o.statut,
            o.prestataire,
            o.ug_ids,
            o.montant_initial::float8 AS montant_initial,
            o.annee_initiale,
            o.montant_ht::float8      AS prevu,
            o.montant_engage::float8  AS engage,
            o.montant_realise::float8 AS realise,
            o.champs_a_confirmer,
            (
                SELECT jsonb_object_agg(t.champ, t.motif)
                FROM (
                    SELECT DISTINCT ON (m.champ) m.champ, m.motif
                    FROM bancarisation.budget_mouvement m
                    WHERE m.occurrence_id = o.id
                      AND m.motif IS NOT NULL
                    ORDER BY m.champ, m.modifie_le DESC
                ) t
            ) AS motifs
        FROM bancarisation.occurrence o
        WHERE o.projet_id = %s
          AND o.annee = %s
          AND o.statut <> 'supprime'
        ORDER BY o.code, o.titre
        """,
        (projet_id, annee),
    )
    lignes = []
    for r in cur.fetchall():
        d = dict(r)
        init = d.get("montant_initial")
        d["ecart_initial"] = (d.get("prevu") or 0.0) - init if init is not None else None
        d["ajoutee_apres_baseline"] = init is None
        lignes.append(d)
    return lignes


def _ecarts(cur: psycopg.Cursor, projet_id: str, annee: int) -> dict[str, Any]:
    cur.execute(
        """
        SELECT annule::float8, glisse_sortant::float8,
               ajoute::float8, revision_prix::float8
        FROM bancarisation.v_budget_ecarts
        WHERE projet_id = %s AND annee_ref = %s
        """,
        (projet_id, annee),
    )
    row = cur.fetchone()
    return dict(row) if row else {
        "annule": 0.0, "glisse_sortant": 0.0, "ajoute": 0.0, "revision_prix": 0.0,
    }


def _pluriannuel(cur: psycopg.Cursor, projet_id: str, annee: int) -> dict[str, Any]:
    cur.execute(
        """
        SELECT annee, initial::float8, prevu::float8,
               engage::float8, realise::float8, delta_prevu_initial::float8
        FROM bancarisation.v_budget_delta_annuel
        WHERE projet_id = %s
        ORDER BY annee
        """,
        (projet_id,),
    )
    annees = [dict(r) for r in cur.fetchall()]
    cumul_realise_fin_annee = sum(a["realise"] for a in annees if a["annee"] <= annee)
    restant_a_engager = sum(
        max(a["prevu"] - a["engage"], 0.0) for a in annees if a["annee"] > annee
    )
    return {
        "annees": annees,
        "cumul_realise_fin_annee": cumul_realise_fin_annee,
        "restant_a_engager_apres_annee": restant_a_engager,
    }


def _journal_annee(cur: psycopg.Cursor, projet_id: str, annee: int) -> list[dict[str, Any]]:
    """Mouvements enregistrés PENDANT l'année civile N (l'activité de l'année)."""
    cur.execute(
        """
        SELECT
            m.modifie_le::text,
            m.champ,
            m.ancienne_val,
            m.nouvelle_val,
            m.motif,
            m.modifie_par,
            o.code  AS occurrence_code,
            o.titre AS occurrence_titre,
            o.annee AS occurrence_annee
        FROM bancarisation.budget_mouvement m
        JOIN bancarisation.occurrence o ON o.id = m.occurrence_id
        WHERE m.projet_id = %s
          AND m.modifie_le >= make_date(%s, 1, 1)
          AND m.modifie_le <  make_date(%s + 1, 1, 1)
        ORDER BY m.modifie_le ASC
        """,
        (projet_id, annee, annee),
    )
    return [dict(r) for r in cur.fetchall()]


def _raccordement(cur: psycopg.Cursor, projet_id: str, annee: int) -> dict[str, Any] | None:
    """Compare les totaux ARCHIVÉS du bilan validé N-1 au recalcul actuel.

    Un écart n'est pas une erreur : c'est l'information qu'une correction est
    intervenue après validation du bilan précédent — à mentionner dans N.
    """
    cur.execute(
        """
        SELECT id::text, version, valide_le::text, donnees
        FROM bancarisation.rapport_bilan
        WHERE projet_id = %s AND annee = %s AND statut = 'valide'
        ORDER BY version DESC
        LIMIT 1
        """,
        (projet_id, annee - 1),
    )
    prec = cur.fetchone()
    if not prec:
        return None

    donnees = prec["donnees"]
    if isinstance(donnees, str):
        donnees = json.loads(donnees)
    archive = (donnees or {}).get("synthese", {})

    recalcul = _synthese(cur, projet_id, annee - 1)
    ecarts = {
        champ: round(recalcul.get(champ, 0.0) - float(archive.get(champ) or 0.0), 2)
        for champ in ("initial", "prevu", "engage", "realise")
    }
    return {
        "bilan_precedent_id": prec["id"],
        "bilan_precedent_version": prec["version"],
        "bilan_precedent_valide_le": prec["valide_le"],
        "totaux_archives": archive,
        "totaux_recalcules": recalcul,
        "ecarts": ecarts,
        "corrections_posterieures": any(abs(v) >= 1 for v in ecarts.values()),
    }


def _ref_occurrence(l: dict[str, Any]) -> dict[str, Any]:
    """Référence affichable d'une occurrence dans un contrôle."""
    ref: dict[str, Any] = {
        "id": l["id"],
        "code": l.get("code") or "",
        "titre": l.get("titre") or "",
        "statut": l.get("statut"),
    }
    if l.get("ecart_initial") is not None:
        ref["ecart_initial"] = l["ecart_initial"]
    return ref


def _controles(
    cur: psycopg.Cursor,
    projet_id: str,
    annee: int,
    lignes: list[dict[str, Any]],
    baseline: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Points relevés avant validation. niveau: bloquant | avertissement."""
    controles: list[dict[str, Any]] = []
    annee_courante = datetime.now(timezone.utc).year

    if baseline is None:
        controles.append({
            "code": "baseline_absente",
            "niveau": "bloquant",
            "libelle": "Aucun budget initial figé : les écarts ne sont pas calculables.",
        })

    non_soldees = [
        l for l in lignes if l["statut"] in ("planifie", "en_cours", "a_confirmer")
    ]
    if non_soldees and annee < annee_courante:
        controles.append({
            "code": "statuts_non_soldes",
            "niveau": "bloquant",
            "libelle": f"{len(non_soldees)} action(s) de {annee} encore non soldée(s) "
                       "(planifiée/en cours) : trancher réalisé ou reporté avant validation.",
            "occurrences": [_ref_occurrence(l) for l in non_soldees],
        })

    sans_motif = [
        l for l in lignes
        if l.get("ecart_initial") not in (None, 0.0)
        and abs(l["ecart_initial"]) >= 1
        and not (l.get("motifs") or {})
    ]
    if sans_motif:
        controles.append({
            "code": "ecarts_sans_motif",
            "niveau": "avertissement",
            "libelle": f"{len(sans_motif)} écart(s) au budget initial sans motif renseigné.",
            "occurrences": [_ref_occurrence(l) for l in sans_motif],
        })

    sans_montant = [l for l in lignes if l.get("prevu") is None]
    if sans_montant:
        controles.append({
            "code": "lignes_sans_montant",
            "niveau": "avertissement",
            "libelle": f"{len(sans_montant)} action(s) de {annee} sans montant prévu.",
            "occurrences": [_ref_occurrence(l) for l in sans_montant],
        })

    a_confirmer = [l for l in lignes if l.get("champs_a_confirmer")]
    if a_confirmer:
        controles.append({
            "code": "champs_a_confirmer",
            "niveau": "avertissement",
            "libelle": f"{len(a_confirmer)} ligne(s) avec des champs issus de l'analyse "
                       "automatique non confirmés.",
            "occurrences": [_ref_occurrence(l) for l in a_confirmer],
        })

    return controles


def construire_bilan(projet_id: UUID, annee: int) -> dict[str, Any]:
    """Construit le snapshot complet du bilan (sans l'archiver)."""
    pid = str(projet_id)
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, figee_le::text, libelle, mode, total_ht::float8
                FROM bancarisation.budget_baseline
                WHERE projet_id = %s
                ORDER BY figee_le DESC LIMIT 1
                """,
                (pid,),
            )
            baseline = cur.fetchone()
            baseline = dict(baseline) if baseline else None

            cur.execute(
                "SELECT max(modifie_le)::text AS borne "
                "FROM bancarisation.budget_mouvement WHERE projet_id = %s",
                (pid,),
            )
            borne = (cur.fetchone() or {}).get("borne")

            lignes = _lignes_annee(cur, pid, annee)
            bilan = {
                "projet_id": pid,
                "annee": annee,
                "genere_le": datetime.now(timezone.utc).isoformat(),
                "borne_donnees": borne,
                "baseline": baseline,
                "synthese": _synthese(cur, pid, annee),
                "lignes": lignes,
                "ecarts": _ecarts(cur, pid, annee),
                "pluriannuel": _pluriannuel(cur, pid, annee),
                "journal": _journal_annee(cur, pid, annee),
                "raccordement_n_moins_1": _raccordement(cur, pid, annee),
            }
            bilan["controles"] = _controles(cur, pid, annee, lignes, baseline)
    return bilan


# ---------------------------------------------------------------------------
# Génération & lecture
# ---------------------------------------------------------------------------

def generer_bilan(
    projet_id: UUID,
    annee: int,
    genere_par: str | None = None,
) -> dict[str, Any]:
    """Construit le bilan et l'archive (version = max + 1). Statut = valide."""
    bilan = construire_bilan(projet_id, annee)
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bancarisation.rapport_bilan
                    (projet_id, annee, version, statut, genere_par,
                     valide_le, valide_par,
                     borne_donnees, baseline_id, donnees, controles)
                VALUES (
                    %s, %s,
                    COALESCE((
                        SELECT max(version) FROM bancarisation.rapport_bilan
                        WHERE projet_id = %s AND annee = %s
                    ), 0) + 1,
                    'valide', %s,
                    now(), %s,
                    %s, %s, %s, %s
                )
                RETURNING id::text, projet_id::text, annee, version, statut,
                          genere_le::text, genere_par, valide_le::text, valide_par,
                          borne_donnees::text
                """,
                (
                    str(projet_id), annee, str(projet_id), annee,
                    genere_par,
                    genere_par,
                    bilan["borne_donnees"],
                    (bilan["baseline"] or {}).get("id"),
                    Jsonb(bilan),
                    Jsonb(bilan["controles"]),
                ),
            )
            row = dict(cur.fetchone())
    row["controles"] = bilan["controles"]
    journaliser(
        action="bilan.generer",
        projet_id=projet_id,
        cible_type="rapport_bilan",
        cible_id=row.get("id"),
        detail={"annee": annee, "version": row.get("version"), "statut": "valide"},
        acteur=genere_par,
    )
    return row


def valider_bilan(
    rapport_id: UUID,
    valide_par: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Valide un brouillon. Refuse si contrôles bloquants, sauf force=True
    (le forçage est lui-même tracé dans les données du bilan)."""
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT statut, controles FROM bancarisation.rapport_bilan WHERE id = %s",
                (str(rapport_id),),
            )
            row = cur.fetchone()
            if row is None:
                raise BilanError("Bilan introuvable.")
            if row["statut"] == "valide":
                raise BilanError("Bilan déjà validé.")

            controles = row["controles"]
            if isinstance(controles, str):
                controles = json.loads(controles)
            bloquants = [c for c in (controles or []) if c.get("niveau") == "bloquant"]
            if bloquants and not force:
                raise BilanError(
                    "Contrôles bloquants non résolus : "
                    + " ; ".join(c["libelle"] for c in bloquants)
                    + " (regénérer après correction, ou valider avec force=true)."
                )

            cur.execute(
                """
                UPDATE bancarisation.rapport_bilan
                SET statut = 'valide',
                    valide_le = now(),
                    valide_par = %s,
                    donnees = donnees || jsonb_build_object('validation_forcee', %s)
                WHERE id = %s AND statut = 'brouillon'
                RETURNING id::text, annee, version, statut, valide_le::text, valide_par
                """,
                (valide_par, bool(bloquants and force), str(rapport_id)),
            )
            out = cur.fetchone()
    if out is None:
        raise BilanError("Validation impossible (état modifié entre-temps).")
    result = dict(out)

    # PDF opposable : rendu du snapshot + dépôt bucket documents/bilans/
    try:
        from .archiver import archiver_pdf_bilan

        archived = archiver_pdf_bilan(rapport_id, remplacer=True)
        result["document_id"] = archived.get("document_id")
        result["pdf_archive"] = True
    except BilanError as exc:
        result["document_id"] = None
        result["pdf_archive"] = False
        result["pdf_erreur"] = str(exc)

    return result


def lister_bilans(projet_id: UUID) -> list[dict[str, Any]]:
    """Liste les bilans du projet (toutes années). Tolère l'absence de document_id."""
    sql_avec_doc = """
        SELECT id::text, annee, version, statut,
               genere_le::text, genere_par,
               valide_le::text, valide_par,
               borne_donnees::text,
               document_id::text,
               COALESCE(jsonb_array_length(
                 CASE
                   WHEN jsonb_typeof(controles) = 'array' THEN controles
                   ELSE '[]'::jsonb
                 END
               ), 0) AS nb_controles
        FROM bancarisation.rapport_bilan
        WHERE projet_id = %s
        ORDER BY annee DESC, version DESC
    """
    sql_sans_doc = """
        SELECT id::text, annee, version, statut,
               genere_le::text, genere_par,
               valide_le::text, valide_par,
               borne_donnees::text,
               NULL::text AS document_id,
               COALESCE(jsonb_array_length(
                 CASE
                   WHEN jsonb_typeof(controles) = 'array' THEN controles
                   ELSE '[]'::jsonb
                 END
               ), 0) AS nb_controles
        FROM bancarisation.rapport_bilan
        WHERE projet_id = %s
        ORDER BY annee DESC, version DESC
    """
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(sql_avec_doc, (str(projet_id),))
            except Exception:
                conn.rollback()
                cur.execute(sql_sans_doc, (str(projet_id),))
            return [dict(r) for r in cur.fetchall()]


def lire_bilan(rapport_id: UUID) -> dict[str, Any]:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, projet_id::text, annee, version, statut,
                       genere_le::text, genere_par, valide_le::text, valide_par,
                       borne_donnees::text, document_id::text, donnees, controles
                FROM bancarisation.rapport_bilan
                WHERE id = %s
                """,
                (str(rapport_id),),
            )
            row = cur.fetchone()
    if row is None:
        raise BilanError("Bilan introuvable.")
    out = dict(row)
    for k in ("donnees", "controles"):
        if isinstance(out.get(k), str):
            out[k] = json.loads(out[k])
    return out


def supprimer_bilan(
    rapport_id: UUID,
    *,
    acteur: str | None = None,
) -> dict[str, Any]:
    """Supprime un bilan généré (+ PDF lié) et trace l'action dans journal_actions."""
    bilan = lire_bilan(rapport_id)

    doc_id = bilan.get("document_id")
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            journaliser(
                action="bilan.supprimer",
                projet_id=bilan.get("projet_id"),
                cible_type="rapport_bilan",
                cible_id=bilan.get("id"),
                detail={
                    "annee": bilan.get("annee"),
                    "version": bilan.get("version"),
                    "statut": bilan.get("statut"),
                    "document_id": doc_id,
                    "genere_le": bilan.get("genere_le"),
                },
                acteur=acteur,
                cur=cur,
            )
            cur.execute(
                """
                DELETE FROM bancarisation.rapport_bilan
                WHERE id = %s
                RETURNING id::text
                """,
                (str(rapport_id),),
            )
            deleted = cur.fetchone()
    if deleted is None:
        raise BilanError("Suppression impossible (bilan déjà supprimé).")

    if doc_id:
        try:
            from api.documents.crud_document import DocumentServiceError, delete_document

            delete_document(UUID(str(doc_id)))
        except Exception:  # noqa: BLE001
            pass

    return {
        "id": deleted["id"],
        "annee": bilan.get("annee"),
        "version": bilan.get("version"),
        "supprime": True,
    }
