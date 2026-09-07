"""Bilan de suivi écologique — construction, génération, dépôt DREAL."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.journal_actions import journaliser
from api.db.env import get_database_url


class SuiviError(Exception):
    pass


STATUTS_SOLDES = frozenset({"realise", "repousse", "supprime"})
STATUTS_OUVERTS = frozenset({"planifie", "en_cours", "a_confirmer"})


def _as_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _libelle_surface(m2: float | None) -> str | None:
    if m2 is None or m2 <= 0.5:
        return None
    ha = m2 / 10_000
    if ha < 0.01:
        return f"{int(round(m2))}\u00a0m²"
    txt = f"{ha:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{txt}\u00a0ha"


def _group_is_postponement(ms: list[dict[str, Any]]) -> bool:
    annee_m = next((m for m in ms if m.get("champ") == "annee"), None)
    debut_m = next((m for m in ms if m.get("champ") == "mois_debut"), None)
    if annee_m:
        old, new = _as_int(annee_m.get("ancienne_val")), _as_int(annee_m.get("nouvelle_val"))
        if old is not None and new is not None:
            if new > old:
                return True
            if new < old:
                return False
    if debut_m:
        old, new = _as_int(debut_m.get("ancienne_val")), _as_int(debut_m.get("nouvelle_val"))
        if old is not None and new is not None and new > old:
            return True
    return False


def _nb_reports_periode(changements: list[dict[str, Any]] | None) -> int:
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in changements or []:
        groups.setdefault(str(m.get("modifie_le") or ""), []).append(m)
    return sum(1 for ms in groups.values() if _group_is_postponement(ms))


def _est_reportee_exercice(l: dict[str, Any], annee: int) -> bool:
    """Action prévue sur N et décalée plus tard (intra-année ou hors exercice)."""
    if l.get("statut") == "repousse":
        return True
    if l.get("sortie_exercice"):
        return True
    if _as_int(l.get("annee_initiale")) == annee and (_as_int(l.get("annee")) or 0) > annee:
        return True
    for m in l.get("changements_periode") or []:
        champ = m.get("champ")
        old, new = _as_int(m.get("ancienne_val")), _as_int(m.get("nouvelle_val"))
        if old is None or new is None:
            continue
        if champ == "annee" and old == annee and new > annee:
            return True
        if champ == "mois_debut" and new > old and not l.get("sortie_exercice"):
            return True
    return False


def _synthese(lignes: list[dict[str, Any]]) -> dict[str, Any]:
    par_statut: dict[str, int] = {}
    for l in lignes:
        if l.get("sortie_exercice"):
            par_statut["reportee_hors_exercice"] = par_statut.get("reportee_hors_exercice", 0) + 1
            continue
        st = str(l.get("statut") or "inconnu")
        par_statut[st] = par_statut.get(st, 0) + 1

    realisees = [
        l for l in lignes
        if l.get("statut") == "realise" and not l.get("sortie_exercice")
    ]
    avec_preuve = [l for l in realisees if (l.get("nb_documents") or 0) > 0]
    sans_preuve = [l for l in realisees if (l.get("nb_documents") or 0) == 0]
    avec_commentaire = [l for l in lignes if (l.get("commentaire_be") or "").strip()]
    reportees = [l for l in lignes if l.get("reportee_exercice")]
    non_realisees = [
        l for l in lignes
        if l.get("statut") in STATUTS_OUVERTS and not l.get("sortie_exercice")
    ]
    reprogrammees = [
        l for l in lignes
        if l.get("sortie_exercice")
        or (l.get("statut") == "repousse" and not l.get("sortie_exercice"))
    ]
    glisses = [
        l for l in lignes
        if l.get("changements_annee") or l.get("sortie_exercice")
    ]
    soldes = [
        l for l in lignes
        if l.get("statut") in STATUTS_SOLDES or l.get("sortie_exercice")
    ]

    return {
        "nb_occurrences": len(lignes),
        "par_statut": par_statut,
        "nb_realisees": len(realisees),
        "nb_avec_preuve": len(avec_preuve),
        "nb_sans_preuve": len(sans_preuve),
        "nb_avec_commentaire": len(avec_commentaire),
        "nb_reportees": len(reportees),
        "nb_non_realisees": len(non_realisees),
        "nb_reprogrammees": len(reprogrammees),
        "nb_glissements_annee": len(glisses),
        "taux_preuve": (
            round(len(avec_preuve) / len(realisees), 4) if realisees else None
        ),
        "taux_soldes": (
            round(len(soldes) / len(lignes), 4) if lignes else None
        ),
    }


def _libelle_realisation(debut: Any, fin: Any) -> str | None:
    """« le 12/03/2026 » ou « du 12/03/2026 au 28/03/2026 »."""
    d = str(debut or "")[:10]
    if not d:
        return None
    f = str(fin or "")[:10]

    def _fmt(iso: str) -> str:
        try:
            return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return iso

    if not f or f == d:
        return f"le {_fmt(d)}"
    return f"du {_fmt(d)} au {_fmt(f)}"


def _lignes_annee(
    cur: psycopg.Cursor,
    projet_id: str,
    annee: int,
    commentaires: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Occurrences de l'année + documents liés + historique de reports.

    Inclut aussi les actions *parties* de N (report hors exercice) : elles
    restent visibles dans le bilan N même si `occurrence.annee` est devenu N+k.
    """
    commentaires = commentaires or {}
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
            o.annee,
            o.annee_initiale,
            o.mois_debut,
            o.mois_fin,
            o.traverse_nouvel_an,
            o.ug_ids,
            o.echeance_id::text,
            o.date_realisation::text,
            (to_jsonb(o) ->> 'date_realisation_fin') AS date_realisation_fin,
            (to_jsonb(o) ->> 'surface_m2')::double precision AS surface_m2,
            (
              SELECT SUM(ST_Area(ST_Transform(s.geom_3857, 2154)))
              FROM bancarisation.unites_de_gestion_surf s
              WHERE s.projet_id = o.projet_id
                AND o.ug_ids IS NOT NULL
                AND s.ug_id = ANY (o.ug_ids)
            ) AS surface_ug_m2,
            o.commentaire AS commentaire_occurrence,
            coalesce(
              (
                SELECT jsonb_agg(jsonb_build_object(
                  'id', d.id::text,
                  'nom', d.nom,
                  'nom_fichier', d.nom_fichier,
                  'categorie', d.categorie,
                  'type_mime', d.type_mime,
                  'date_document', d.date_document::text
                ) ORDER BY d.created_at DESC)
                FROM bancarisation.documents d
                WHERE d.occurrence_id = o.id
              ),
              '[]'::jsonb
            ) AS documents,
            (
              SELECT count(*)::int
              FROM bancarisation.documents d
              WHERE d.occurrence_id = o.id
            ) AS nb_documents,
            (
              SELECT jsonb_agg(jsonb_build_object(
                'ancienne_val', m.ancienne_val,
                'nouvelle_val', m.nouvelle_val,
                'motif', m.motif,
                'modifie_par', m.modifie_par,
                'modifie_le', m.modifie_le::text
              ) ORDER BY m.modifie_le DESC)
              FROM bancarisation.budget_mouvement m
              WHERE m.occurrence_id = o.id
                AND m.champ = 'annee'
                AND m.motif IS NOT NULL
            ) AS changements_annee,
            (
              SELECT jsonb_agg(jsonb_build_object(
                'ancienne_val', m.ancienne_val,
                'nouvelle_val', m.nouvelle_val,
                'motif', m.motif,
                'modifie_par', m.modifie_par,
                'modifie_le', m.modifie_le::text
              ) ORDER BY m.modifie_le DESC)
              FROM bancarisation.budget_mouvement m
              WHERE m.occurrence_id = o.id
                AND m.champ = 'statut'
                AND m.motif IS NOT NULL
            ) AS changements_statut,
            (
              SELECT jsonb_agg(jsonb_build_object(
                'champ', m.champ,
                'ancienne_val', m.ancienne_val,
                'nouvelle_val', m.nouvelle_val,
                'motif', m.motif,
                'modifie_par', m.modifie_par,
                'modifie_le', m.modifie_le::text
              ) ORDER BY m.modifie_le DESC)
              FROM bancarisation.budget_mouvement m
              WHERE m.occurrence_id = o.id
                AND m.champ IN ('annee', 'mois_debut', 'mois_fin')
            ) AS changements_periode
        FROM bancarisation.occurrence o
        WHERE o.projet_id = %s
          AND o.statut <> 'supprime'
          AND (
            -- Année d'exercice N, ou débordement N-1 → N si traverse le nouvel an.
            o.annee = %s
            OR (
              o.traverse_nouvel_an = true
              AND o.annee = %s - 1
              AND o.mois_debut IS NOT NULL
              AND o.mois_fin IS NOT NULL
              AND o.mois_debut > o.mois_fin
            )
            -- Report hors de N : l'occurrence a quitté l'exercice (journal).
            OR EXISTS (
              SELECT 1 FROM bancarisation.budget_mouvement m
              WHERE m.occurrence_id = o.id
                AND m.champ = 'annee'
                AND m.ancienne_val = %s
                AND m.nouvelle_val ~ '^[0-9]+$'
                AND m.nouvelle_val::integer > %s
            )
            -- Fallback baseline (données antérieures au journal de période).
            OR (o.annee_initiale = %s AND o.annee > %s)
          )
        ORDER BY o.annee, o.mois_debut NULLS LAST, o.categorie NULLS LAST, o.code, o.titre
        """,
        (projet_id, annee, annee, str(annee), annee, annee, annee),
    )
    lignes: list[dict[str, Any]] = []
    for r in cur.fetchall():
        d = dict(r)
        docs = d.get("documents") or []
        if isinstance(docs, str):
            docs = json.loads(docs)
        d["documents"] = docs if isinstance(docs, list) else []
        for key in ("changements_annee", "changements_statut", "changements_periode"):
            val = d.get(key)
            if isinstance(val, str):
                val = json.loads(val)
            d[key] = val if isinstance(val, list) else []

        oid = d["id"]
        # Priorité : commentaire passé à la génération > commentaire occurrence
        override = (commentaires.get(oid) or "").strip()
        base = (d.get("commentaire_occurrence") or "").strip()
        d["commentaire_be"] = override or base or None
        annee_occ = int(d.get("annee") or 0)
        # Visible en année civile N alors que l'exercice stocké est N-1 (traverse nouvel an).
        d["chevauchement_annee_civile"] = annee_occ != annee and annee_occ == annee - 1
        d["sortie_exercice"] = annee_occ > annee
        d["nb_reports"] = _nb_reports_periode(d.get("changements_periode"))
        d["reportee_exercice"] = _est_reportee_exercice(d, annee)
        d["libelle_realisation"] = _libelle_realisation(
            d.get("date_realisation"), d.get("date_realisation_fin"),
        )
        saisie = _as_float(d.get("surface_m2"))
        ug_m2 = _as_float(d.get("surface_ug_m2"))
        effective = saisie if saisie is not None else ug_m2
        d["surface_m2"] = saisie
        d["surface_ug_m2"] = ug_m2
        d["surface_effective_m2"] = effective
        d["surface_source"] = "saisie" if saisie is not None else "ug"
        d["libelle_surface"] = _libelle_surface(effective)
        if d["sortie_exercice"] or d.get("statut") == "repousse":
            d["volet"] = "reprogrammee"
        elif d.get("statut") == "realise":
            d["volet"] = "realisee"
        else:
            d["volet"] = "non_realisee"
        lignes.append(d)
    return lignes


def _journal_suivi(cur: psycopg.Cursor, projet_id: str, annee: int) -> list[dict[str, Any]]:
    """Mouvements d'année / période / statut pendant l'année civile N."""
    cur.execute(
        """
        SELECT
            m.modifie_le::text,
            m.champ,
            m.ancienne_val,
            m.nouvelle_val,
            m.motif,
            m.modifie_par,
            o.id::text AS occurrence_id,
            o.code AS occurrence_code,
            o.titre AS occurrence_titre,
            o.annee AS occurrence_annee
        FROM bancarisation.budget_mouvement m
        JOIN bancarisation.occurrence o ON o.id = m.occurrence_id
        WHERE m.projet_id = %s
          AND m.champ IN ('annee', 'mois_debut', 'mois_fin', 'statut')
          AND m.modifie_le >= make_date(%s, 1, 1)
          AND m.modifie_le <  make_date(%s + 1, 1, 1)
        ORDER BY m.modifie_le ASC
        """,
        (projet_id, annee, annee),
    )
    return [dict(r) for r in cur.fetchall()]


def _lignes_n1(
    cur: psycopg.Cursor,
    projet_id: str,
    annee: int,
) -> list[dict[str, Any]]:
    """Prévisionnel de l'année suivante (N+1), pour le bilan de N."""
    cur.execute(
        """
        SELECT
            o.id::text,
            o.code,
            o.titre,
            o.categorie,
            o.statut,
            o.annee,
            o.annee_initiale,
            o.mois_debut,
            o.mois_fin,
            o.traverse_nouvel_an,
            o.prestataire,
            o.ug_ids
        FROM bancarisation.occurrence o
        WHERE o.projet_id = %s
          AND o.annee = %s
          AND o.statut <> 'supprime'
        ORDER BY o.mois_debut NULLS LAST, o.categorie NULLS LAST, o.code, o.titre
        """,
        (projet_id, annee + 1),
    )
    lignes: list[dict[str, Any]] = []
    for r in cur.fetchall():
        d = dict(r)
        ugs = d.get("ug_ids")
        if isinstance(ugs, str):
            try:
                ugs = json.loads(ugs)
            except json.JSONDecodeError:
                ugs = []
        d["ug_ids"] = ugs if isinstance(ugs, list) else []
        d["reprogrammee_depuis_n"] = (
            _as_int(d.get("annee_initiale")) == annee
            and _as_int(d.get("annee")) == annee + 1
        )
        lignes.append(d)
    return lignes


def _controles(lignes: list[dict[str, Any]], annee: int) -> list[dict[str, Any]]:
    controles: list[dict[str, Any]] = []
    annee_courante = datetime.now(timezone.utc).year

    def _ref(l: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": l["id"],
            "code": l.get("code") or "",
            "titre": l.get("titre") or "",
            "statut": l.get("statut"),
            "nb_documents": l.get("nb_documents") or 0,
        }

    non_soldees = [
        l for l in lignes
        if l.get("statut") in STATUTS_OUVERTS and not l.get("sortie_exercice")
    ]
    if non_soldees and annee < annee_courante:
        controles.append({
            "code": "statuts_non_soldes",
            "niveau": "bloquant",
            "libelle": (
                f"{len(non_soldees)} action(s) de {annee} encore non soldée(s) "
                "(planifiée / en cours / à confirmer)."
            ),
            "occurrences": [_ref(l) for l in non_soldees],
        })

    sans_preuve = [
        l for l in lignes
        if l.get("statut") == "realise" and (l.get("nb_documents") or 0) == 0
    ]
    if sans_preuve:
        controles.append({
            "code": "realise_sans_preuve",
            "niveau": "avertissement",
            "libelle": (
                f"{len(sans_preuve)} action(s) marquée(s) réalisée(s) sans pièce jointe."
            ),
            "occurrences": [_ref(l) for l in sans_preuve],
        })

    sans_commentaire = [
        l for l in lignes
        if l.get("statut") == "realise" and not (l.get("commentaire_be") or "").strip()
    ]
    if sans_commentaire:
        controles.append({
            "code": "realise_sans_commentaire",
            "niveau": "avertissement",
            "libelle": (
                f"{len(sans_commentaire)} action(s) réalisée(s) sans précision BE."
            ),
            "occurrences": [_ref(l) for l in sans_commentaire],
        })

    glisse_sans_motif = [
        l for l in lignes
        if l.get("annee_initiale") is not None
        and l.get("annee") != l.get("annee_initiale")
        and not l.get("changements_annee")
    ]
    if glisse_sans_motif:
        controles.append({
            "code": "glissement_sans_motif",
            "niveau": "avertissement",
            "libelle": (
                f"{len(glisse_sans_motif)} action(s) décalée(s) d'exercice sans motif tracé."
            ),
            "occurrences": [_ref(l) for l in glisse_sans_motif],
        })

    return controles


def construire_bilan_suivi(
    projet_id: UUID,
    annee: int,
    *,
    commentaires: dict[str, str] | None = None,
    commentaire_synthese: str | None = None,
) -> dict[str, Any]:
    """Construit le snapshot (sans archiver)."""
    pid = str(projet_id)
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, nom, reference_interne
                FROM bancarisation.projets WHERE id = %s
                """,
                (pid,),
            )
            projet = cur.fetchone()
            if not projet:
                raise SuiviError("Projet introuvable.")

            cur.execute(
                """
                SELECT max(modifie_le)::text AS borne
                FROM bancarisation.budget_mouvement
                WHERE projet_id = %s AND champ IN ('annee', 'mois_debut', 'mois_fin', 'statut')
                """,
                (pid,),
            )
            borne = (cur.fetchone() or {}).get("borne")

            lignes = _lignes_annee(cur, pid, annee, commentaires)
            lignes_n1 = _lignes_n1(cur, pid, annee)
            synthese = _synthese(lignes)
            synthese["nb_prevu_n1"] = len(lignes_n1)
            bilan = {
                "projet_id": pid,
                "projet_nom": projet.get("nom"),
                "reference_interne": projet.get("reference_interne"),
                "annee": annee,
                "genere_le": datetime.now(timezone.utc).isoformat(),
                "borne_donnees": borne,
                "commentaire_synthese": (commentaire_synthese or "").strip() or None,
                "synthese": synthese,
                "lignes": lignes,
                "lignes_n1": lignes_n1,
                "journal": _journal_suivi(cur, pid, annee),
                # Réservé : volet budget / indicateurs satellite
                "volet_budget": None,
                "indicateurs_externes": [],
            }
            bilan["controles"] = _controles(lignes, annee)
    return bilan


def _upsert_bilan_suivi(
    cur: psycopg.Cursor,
    projet_id: str,
    annee: int,
    *,
    document_id: str | None,
    rapport_suivi_id: str,
) -> str:
    """Crée ou met à jour l'enveloppe d'instruction → statut depose.

    Remet aussi statue_le à NULL (nouvelle instruction après complément / rejet).
    """
    cur.execute(
        """
        INSERT INTO bancarisation.bilan_suivi
          (projet_id, annee, statut, depose_le, document_id, rapport_suivi_id)
        VALUES (%s, %s, 'depose', now(), %s::uuid, %s::uuid)
        ON CONFLICT (projet_id, annee) DO UPDATE SET
          statut = 'depose',
          depose_le = now(),
          statue_le = NULL,
          document_id = coalesce(EXCLUDED.document_id, bancarisation.bilan_suivi.document_id),
          rapport_suivi_id = EXCLUDED.rapport_suivi_id
        RETURNING id::text
        """,
        (projet_id, annee, document_id, rapport_suivi_id),
    )
    row = cur.fetchone()
    if not row:
        raise SuiviError("Impossible de créer l'enveloppe bilan_suivi.")
    return row["id"]


def lister_enveloppes_suivi(projet_id: UUID) -> list[dict[str, Any]]:
    """Enveloppes d'instruction (bilan_suivi) — visibles côté BE pour les actions à faire."""
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  id::text,
                  projet_id::text,
                  annee,
                  statut,
                  depose_le::text,
                  statue_le::text,
                  document_id::text,
                  rapport_suivi_id::text
                FROM bancarisation.bilan_suivi
                WHERE projet_id = %s
                ORDER BY annee DESC
                """,
                (str(projet_id),),
            )
            return [dict(r) for r in cur.fetchall()]


def deposer_rapport_suivi(
    rapport_id: UUID,
    *,
    acteur: str | None = None,
) -> dict[str, Any]:
    """Pousse un rapport déjà généré vers la bannette DREAL (bilan_suivi)."""
    rapport = lire_rapport_suivi(rapport_id)
    pid = str(rapport["projet_id"])
    annee = int(rapport["annee"])

    # Idempotence : déjà déposé et enveloppe encore en cours d'instruction « normale »
    if rapport.get("bilan_suivi_id"):
        with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT statut FROM bancarisation.bilan_suivi
                    WHERE id = %s::uuid
                    """,
                    (rapport["bilan_suivi_id"],),
                )
                env = cur.fetchone()
        if env and env.get("statut") in ("depose", "en_relecture", "valide"):
            return {
                **{k: rapport[k] for k in (
                    "id", "projet_id", "annee", "version", "statut",
                    "genere_le", "depose_le", "document_id", "bilan_suivi_id",
                ) if k in rapport},
                "deja_depose": True,
                "controles": rapport.get("controles") or [],
            }

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            bilan_suivi_id = _upsert_bilan_suivi(
                cur,
                pid,
                annee,
                document_id=rapport.get("document_id"),
                rapport_suivi_id=str(rapport_id),
            )
            cur.execute(
                """
                UPDATE bancarisation.rapport_suivi
                SET statut = 'depose',
                    depose_le = coalesce(depose_le, now()),
                    bilan_suivi_id = %s::uuid
                WHERE id = %s::uuid
                RETURNING id::text, projet_id::text, annee, version, statut,
                          genere_le::text, genere_par, depose_le::text,
                          document_id::text, bilan_suivi_id::text
                """,
                (bilan_suivi_id, str(rapport_id)),
            )
            row = dict(cur.fetchone() or {})
            journaliser(
                action="bilan_suivi.deposer",
                projet_id=pid,
                cible_type="rapport_suivi",
                cible_id=str(rapport_id),
                detail={
                    "annee": annee,
                    "version": row.get("version"),
                    "bilan_suivi_id": bilan_suivi_id,
                },
                acteur=acteur,
                cur=cur,
            )
            conn.commit()

    row["controles"] = rapport.get("controles") or []
    row["deja_depose"] = False
    return row


def generer_bilan_suivi(
    projet_id: UUID,
    annee: int,
    *,
    genere_par: str | None = None,
    commentaires: dict[str, str] | None = None,
    commentaire_synthese: str | None = None,
    deposer: bool = False,
) -> dict[str, Any]:
    """Archive une nouvelle version (PDF). Le dépôt DREAL est une étape séparée."""
    bilan = construire_bilan_suivi(
        projet_id,
        annee,
        commentaires=commentaires,
        commentaire_synthese=commentaire_synthese,
    )
    pid = str(projet_id)

    # Persiste les commentaires BE sur les occurrences (source de vérité pour plus tard)
    if commentaires:
        with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                for oid, texte in commentaires.items():
                    if not oid:
                        continue
                    cur.execute(
                        """
                        UPDATE bancarisation.occurrence
                        SET commentaire = %s
                        WHERE id = %s::uuid AND projet_id = %s::uuid
                        """,
                        ((texte or "").strip() or None, oid, pid),
                    )
                conn.commit()

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bancarisation.rapport_suivi
                  (projet_id, annee, version, statut, genere_par,
                   borne_donnees, donnees, controles, depose_le)
                VALUES (
                  %s, %s,
                  COALESCE((
                    SELECT max(version) FROM bancarisation.rapport_suivi
                    WHERE projet_id = %s AND annee = %s
                  ), 0) + 1,
                  %s, %s,
                  %s::timestamptz, %s, %s,
                  CASE WHEN %s THEN now() ELSE NULL END
                )
                RETURNING id::text, projet_id::text, annee, version, statut,
                          genere_le::text, genere_par, depose_le::text,
                          document_id::text, bilan_suivi_id::text
                """,
                (
                    pid,
                    annee,
                    pid,
                    annee,
                    "depose" if deposer else "genere",
                    genere_par,
                    bilan.get("borne_donnees"),
                    Jsonb(bilan),
                    Jsonb(bilan["controles"]),
                    deposer,
                ),
            )
            row = dict(cur.fetchone())
            rapport_id = row["id"]

            bilan_suivi_id = None
            if deposer:
                bilan_suivi_id = _upsert_bilan_suivi(
                    cur,
                    pid,
                    annee,
                    document_id=None,
                    rapport_suivi_id=rapport_id,
                )
                cur.execute(
                    """
                    UPDATE bancarisation.rapport_suivi
                    SET bilan_suivi_id = %s::uuid
                    WHERE id = %s::uuid
                    RETURNING bilan_suivi_id::text
                    """,
                    (bilan_suivi_id, rapport_id),
                )
                row["bilan_suivi_id"] = (cur.fetchone() or {}).get("bilan_suivi_id")

            conn.commit()

    row["controles"] = bilan["controles"]
    journaliser(
        action="bilan_suivi.generer",
        projet_id=projet_id,
        cible_type="rapport_suivi",
        cible_id=row.get("id"),
        detail={
            "annee": annee,
            "version": row.get("version"),
            "statut": row.get("statut"),
            "depose": deposer,
        },
        acteur=genere_par,
    )

    # PDF opposable
    try:
        from .archiver import archiver_pdf_suivi

        archived = archiver_pdf_suivi(UUID(str(row["id"])), remplacer=True)
        row["document_id"] = archived.get("document_id")
        row["pdf_archive"] = True
    except Exception as exc:  # noqa: BLE001
        row["document_id"] = None
        row["pdf_archive"] = False
        row["pdf_erreur"] = str(exc)

    return row


def lister_rapports_suivi(projet_id: UUID) -> list[dict[str, Any]]:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, annee, version, statut,
                       genere_le::text, genere_par,
                       depose_le::text,
                       document_id::text,
                       bilan_suivi_id::text,
                       COALESCE(jsonb_array_length(
                         CASE WHEN jsonb_typeof(controles) = 'array' THEN controles
                              ELSE '[]'::jsonb END
                       ), 0) AS nb_controles
                FROM bancarisation.rapport_suivi
                WHERE projet_id = %s
                ORDER BY annee DESC, version DESC
                """,
                (str(projet_id),),
            )
            return [dict(r) for r in cur.fetchall()]


def lire_rapport_suivi(rapport_id: UUID) -> dict[str, Any]:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, projet_id::text, annee, version, statut,
                       genere_le::text, genere_par, depose_le::text,
                       borne_donnees::text, document_id::text,
                       bilan_suivi_id::text, donnees, controles
                FROM bancarisation.rapport_suivi
                WHERE id = %s
                """,
                (str(rapport_id),),
            )
            row = cur.fetchone()
    if row is None:
        raise SuiviError("Rapport de suivi introuvable.")
    out = dict(row)
    for k in ("donnees", "controles"):
        if isinstance(out.get(k), str):
            out[k] = json.loads(out[k])
    return out


def supprimer_rapport_suivi(
    rapport_id: UUID,
    *,
    acteur: str | None = None,
) -> dict[str, Any]:
    rapport = lire_rapport_suivi(rapport_id)
    doc_id = rapport.get("document_id")
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            journaliser(
                action="bilan_suivi.supprimer",
                projet_id=rapport.get("projet_id"),
                cible_type="rapport_suivi",
                cible_id=rapport.get("id"),
                detail={
                    "annee": rapport.get("annee"),
                    "version": rapport.get("version"),
                    "statut": rapport.get("statut"),
                    "document_id": doc_id,
                },
                acteur=acteur,
                cur=cur,
            )
            # Détache l'enveloppe si c'était la version liée
            if rapport.get("bilan_suivi_id"):
                cur.execute(
                    """
                    UPDATE bancarisation.bilan_suivi
                    SET rapport_suivi_id = NULL,
                        statut = CASE
                          WHEN statut IN ('depose', 'en_relecture') THEN 'attendu'
                          ELSE statut
                        END
                    WHERE id = %s::uuid AND rapport_suivi_id = %s::uuid
                    """,
                    (rapport["bilan_suivi_id"], str(rapport_id)),
                )
            cur.execute(
                "DELETE FROM bancarisation.rapport_suivi WHERE id = %s RETURNING id::text",
                (str(rapport_id),),
            )
            deleted = cur.fetchone()
            conn.commit()
    if deleted is None:
        raise SuiviError("Suppression impossible.")

    if doc_id:
        try:
            from api.documents.crud_document import delete_document

            delete_document(UUID(str(doc_id)))
        except Exception:  # noqa: BLE001
            pass

    return {
        "id": deleted["id"],
        "annee": rapport.get("annee"),
        "version": rapport.get("version"),
        "supprime": True,
    }
