"""Requêtes parc sur les vues v_parc_* (filtrage d'habilitation obligatoire)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from api.db.env import get_database_url
from api.parc.filtre import filtre_organisation
from api.parc.schemas import (
    CaseBilanMatrice,
    ProjetParc,
    SignalCompte,
    SignalParc,
    SyntheseParc,
)


def _parse_signaux(raw: Any) -> list[SignalParc]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list):
        return []
    out: list[SignalParc] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            SignalParc(
                code=item.get("code", ""),
                niveau=item.get("niveau", ""),
                libelle=item.get("libelle", ""),
                valeur=float(item["valeur"]) if item.get("valeur") is not None else None,
                detail=item.get("detail"),
            )
        )
    return out


def _row_to_projet(row: dict[str, Any]) -> ProjetParc:
    return ProjetParc(
        projet_id=row["projet_id"],
        nom=row["nom"],
        reference_interne=row.get("reference_interne"),
        organisation_id=row["organisation_id"],
        organisation_nom=row["organisation_nom"],
        commune=row.get("commune"),
        departement=row.get("departement"),
        type_procedure=row.get("type_procedure"),
        statut=row["statut"],
        date_decision=row.get("date_decision"),
        duree_annees=row.get("duree_annees"),
        date_fin=row.get("date_fin"),
        gravite=int(row.get("gravite") or 0),
        nb_signaux_critiques=int(row.get("nb_signaux_critiques") or 0),
        nb_signaux_attention=int(row.get("nb_signaux_attention") or 0),
        signaux=_parse_signaux(row.get("signaux")),
        total_initial=float(row.get("total_initial") or 0),
        total_prevu=float(row.get("total_prevu") or 0),
        total_engage=float(row.get("total_engage") or 0),
        total_realise=float(row.get("total_realise") or 0),
        delta_total=float(row.get("delta_total") or 0),
        prevu_annee_courante=float(row.get("prevu_annee_courante") or 0),
        realise_annee_courante=float(row.get("realise_annee_courante") or 0),
        premiere_annee=row.get("premiere_annee"),
        derniere_annee=row.get("derniere_annee"),
        nb_occurrences=int(row.get("nb_occurrences") or 0),
        nb_occurrences_realisees=int(row.get("nb_occurrences_realisees") or 0),
        nb_occurrences_reportees=int(row.get("nb_occurrences_reportees") or 0),
        nb_bilans_valides=int(row.get("nb_bilans_valides") or 0),
        nb_bilans_manquants=int(row.get("nb_bilans_manquants") or 0),
        dernier_bilan_valide=row.get("dernier_bilan_valide"),
    )


def lister_projets_parc(
    *,
    role: str,
    organisation_id: UUID | None,
    organisation_filtre: UUID | None = None,
    departement: str | None = None,
    gravite: int | None = None,
    signal: str | None = None,
    statut: str | None = None,
    q: str | None = None,
    file_controle_only: bool = False,
) -> list[ProjetParc]:
    clause, params = filtre_organisation(role, organisation_id)
    where = [f"({clause})"]
    args: list[Any] = list(params)

    if organisation_filtre is not None:
        where.append("organisation_id = %s")
        args.append(str(organisation_filtre))
    if departement:
        where.append("departement = %s")
        args.append(departement)
    if gravite is not None:
        where.append("gravite = %s")
        args.append(gravite)
    if statut:
        where.append("statut = %s")
        args.append(statut)
    if q:
        where.append("(nom ILIKE %s OR coalesce(reference_interne, '') ILIKE %s)")
        like = f"%{q}%"
        args.extend([like, like])
    if signal:
        where.append(
            "EXISTS ("
            "  SELECT 1 FROM jsonb_array_elements(signaux) s"
            "  WHERE s->>'code' = %s"
            ")"
        )
        args.append(signal)
    if file_controle_only:
        where.append("gravite > 0")

    sql = f"""
        SELECT
            projet_id, nom, reference_interne, organisation_id, organisation_nom,
            commune, departement, type_procedure, statut,
            date_decision, duree_annees, date_fin,
            gravite, nb_signaux_critiques, nb_signaux_attention, signaux,
            total_initial::float8, total_prevu::float8,
            total_engage::float8, total_realise::float8, delta_total::float8,
            prevu_annee_courante::float8, realise_annee_courante::float8,
            premiere_annee, derniere_annee,
            nb_occurrences, nb_occurrences_realisees, nb_occurrences_reportees,
            nb_bilans_valides, nb_bilans_manquants, dernier_bilan_valide
        FROM bancarisation.v_parc_projet
        WHERE {' AND '.join(where)}
        ORDER BY
            gravite DESC,
            nb_signaux_critiques DESC,
            nb_signaux_attention DESC,
            total_prevu DESC
    """
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
    return [_row_to_projet(dict(r)) for r in rows]


def lire_synthese_parc(
    *,
    role: str,
    organisation_id: UUID | None,
) -> SyntheseParc:
    clause, params = filtre_organisation(role, organisation_id)
    args = list(params)

    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    count(*)::int AS nb_projets,
                    count(DISTINCT organisation_id)::int AS nb_organisations,
                    coalesce(sum(total_prevu), 0)::float8 AS budget_total_sous_obligation,
                    coalesce(sum(total_realise), 0)::float8 AS realise_cumule,
                    coalesce(sum(realise_annee_courante), 0)::float8 AS realise_annee_courante,
                    count(*) FILTER (WHERE gravite = 0)::int AS nb_projets_conformes,
                    count(*) FILTER (WHERE gravite = 1)::int AS nb_projets_attention,
                    count(*) FILTER (WHERE gravite = 2)::int AS nb_projets_critiques,
                    coalesce(sum(nb_bilans_valides), 0)::int AS bilans_valides,
                    coalesce(sum(nb_bilans_manquants), 0)::int AS bilans_manquants
                FROM bancarisation.v_parc_projet
                WHERE ({clause})
                """,
                args,
            )
            agg = dict(cur.fetchone() or {})

            cur.execute(
                f"""
                SELECT
                    s.code,
                    count(DISTINCT s.projet_id)::int AS nb_projets,
                    count(DISTINCT s.projet_id) FILTER (WHERE s.niveau = 'critique')::int
                        AS nb_critiques
                FROM bancarisation.v_parc_signal s
                JOIN bancarisation.v_parc_projet p ON p.projet_id = s.projet_id
                WHERE ({clause})
                GROUP BY s.code
                ORDER BY s.code
                """,
                args,
            )
            signaux = [
                SignalCompte(
                    code=r["code"],
                    nb_projets=int(r["nb_projets"]),
                    nb_critiques=int(r["nb_critiques"] or 0),
                )
                for r in cur.fetchall()
            ]

    bilans_valides = int(agg.get("bilans_valides") or 0)
    bilans_manquants = int(agg.get("bilans_manquants") or 0)
    bilans_attendus = bilans_valides + bilans_manquants
    taux = (
        round(bilans_valides / bilans_attendus, 4) if bilans_attendus > 0 else None
    )

    return SyntheseParc(
        nb_projets=int(agg.get("nb_projets") or 0),
        nb_organisations=int(agg.get("nb_organisations") or 0),
        budget_total_sous_obligation=float(agg.get("budget_total_sous_obligation") or 0),
        realise_cumule=float(agg.get("realise_cumule") or 0),
        realise_annee_courante=float(agg.get("realise_annee_courante") or 0),
        nb_projets_conformes=int(agg.get("nb_projets_conformes") or 0),
        nb_projets_attention=int(agg.get("nb_projets_attention") or 0),
        nb_projets_critiques=int(agg.get("nb_projets_critiques") or 0),
        bilans_attendus=bilans_attendus,
        bilans_valides=bilans_valides,
        taux_remise_bilans=taux,
        signaux_par_code=signaux,
    )


def lister_bilans_matrice(
    *,
    role: str,
    organisation_id: UUID | None,
    annee_min: int | None = None,
    annee_max: int | None = None,
    organisation_filtre: UUID | None = None,
) -> list[CaseBilanMatrice]:
    clause, params = filtre_organisation(role, organisation_id)
    # La vue matrice n'a pas organisation_nom : jointure projets/orgs.
    # Le filtre porte sur m.organisation_id.
    where = [f"({clause.replace('organisation_id', 'm.organisation_id')})"]
    args: list[Any] = list(params)

    if organisation_filtre is not None:
        where.append("m.organisation_id = %s")
        args.append(str(organisation_filtre))
    if annee_min is not None:
        where.append("m.annee >= %s")
        args.append(annee_min)
    if annee_max is not None:
        where.append("m.annee <= %s")
        args.append(annee_max)

    sql = f"""
        SELECT
            m.projet_id,
            p.nom AS projet_nom,
            m.organisation_id,
            org.nom AS organisation_nom,
            m.annee,
            m.etat,
            m.rapport_id,
            m.version,
            m.valide_le
        FROM bancarisation.v_parc_bilan_matrice m
        JOIN bancarisation.projets p ON p.id = m.projet_id
        JOIN bancarisation.organisations org ON org.id = m.organisation_id
        WHERE {' AND '.join(where)}
        ORDER BY org.nom, p.nom, m.annee
    """
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()

    return [
        CaseBilanMatrice(
            projet_id=r["projet_id"],
            projet_nom=r["projet_nom"],
            organisation_id=r["organisation_id"],
            organisation_nom=r["organisation_nom"],
            annee=int(r["annee"]),
            etat=r["etat"],
            rapport_id=r.get("rapport_id"),
            version=r.get("version"),
            valide_le=r.get("valide_le"),
        )
        for r in rows
    ]
