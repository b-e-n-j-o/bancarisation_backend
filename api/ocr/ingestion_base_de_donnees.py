#!/usr/bin/env python3
"""
ingestion_base_de_donnees.py — Semis atomique en base après les 3 extractions LLM.

Persiste 4 domaines rattachés au même projet_id (= bancarisation.projets.id) :
  1. projet_metadata  — métadonnées dossier (DossierResult)
  2. action_fiche     — fiches-actions intégrales (ActionsResult)
  3. echeance         — règles récurrentes (EcheancesLieesResult)
  4. occurrence       — instances calendrier (moteur calcul_occurrences)

Usage standalone :
    python ingestion_base_de_donnees.py --creer-projet
    python ingestion_base_de_donnees.py --projet <uuid>
    python ingestion_base_de_donnees.py --projet <uuid> --replace
    python ingestion_base_de_donnees.py --creer-projet --dry-run

Prérequis : DATABASE_URL + migration sql/001_bancarisation_extraction.sql appliquée.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .db_env import get_database_url, load_db_env
from .models import (
    ActionsResult,
    DossierResult,
    EcheancesLieesResult,
    ExtractionResult,
    Occurrence,
)

_SCRIPT_DIR = Path(__file__).resolve().parent

load_db_env()

log = logging.getLogger("ingestion")

DEFAULT_DOSSIER = _SCRIPT_DIR / "dossier.json"
DEFAULT_ACTIONS = _SCRIPT_DIR / "actions.json"
DEFAULT_ECHEANCES = _SCRIPT_DIR / "echeances_liees.json"
DEFAULT_OCCURRENCES = _SCRIPT_DIR / "occurrences.json"

ORGANISATION_ID_V0 = "a1000000-0000-0000-0000-000000000001"


def connect() -> psycopg.Connection:
    return psycopg.connect(get_database_url(), row_factory=dict_row)


def _verifier_projet(conn: psycopg.Connection, projet_id: UUID | str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from bancarisation.projets where id = %s",
            (projet_id,),
        )
        if not cur.fetchone():
            raise ValueError(f"Projet introuvable : {projet_id}")


def creer_projet(
    conn: psycopg.Connection,
    *,
    dossier: DossierResult | None = None,
    nom: str | None = None,
    reference_interne: str | None = None,
) -> str:
    """Crée un projet minimal dans bancarisation.projets (pré-rempli depuis dossier.json)."""
    meta = dossier.dossier if dossier else None
    nom_final = nom or (
        meta.nom_operation if meta and meta.nom_operation else "Projet sans nom"
    )
    ref = reference_interne
    if not ref and meta and meta.arrete_numero:
        ref = meta.arrete_numero
    commune = meta.communes[0] if meta and meta.communes else None
    duree = meta.horizon.duree_ans if meta else None

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into bancarisation.projets (
                organisation_id, nom, reference_interne, commune, duree_annees, statut
            ) values (%s, %s, %s, %s, %s, 'en_instruction')
            returning id
            """,
            (ORGANISATION_ID_V0, nom_final, ref, commune, duree),
        )
        projet_id = str(cur.fetchone()["id"])
    log.info("Projet créé : %s (%s)", projet_id, nom_final)
    return projet_id


def resoudre_projet_id(
    conn: psycopg.Connection,
    *,
    projet_id: str | None = None,
    creer: bool = False,
    dossier: DossierResult | None = None,
    nom: str | None = None,
) -> str:
    """Utilise un projet existant ou en crée un nouveau."""
    if projet_id:
        _verifier_projet(conn, projet_id)
        return projet_id
    if creer:
        return creer_projet(conn, dossier=dossier, nom=nom)
    raise ValueError("Fournis --projet <uuid> ou --creer-projet")


def ingérer(
    conn: psycopg.Connection,
    projet_id: UUID | str,
    *,
    dossier: DossierResult | None,
    actions: ActionsResult,
    echeances_liees: EcheancesLieesResult,
    occurrences: list[Occurrence],
    fichier_nom: str,
    fichier_hash: str | None = None,
    modele_ocr: str | None = None,
    modele_llm: str | None = None,
    nb_non_placables: int = 0,
    replace: bool = False,
) -> dict[str, Any]:
    """
    Sème métadonnées + actions + échéances + occurrences en UNE transaction.

    replace=False : refuse si le projet a déjà des occurrences IA.
    replace=True  : re-sème les occurrences IA non modifiées par l'utilisateur.
    """
    _verifier_projet(conn, projet_id)
    echeances = echeances_liees.echeances

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                select count(*) as n
                from bancarisation.occurrence
                where projet_id = %s and origine = 'ia'
                """,
                (projet_id,),
            )
            deja = cur.fetchone()["n"]

            if deja and not replace:
                raise ValueError(
                    f"Le projet {projet_id} contient déjà {deja} occurrence(s) IA. "
                    "Relance avec replace=True (les lignes modifiées ou créées par "
                    "l'utilisateur seront préservées)."
                )

            supprimees = 0
            if replace and deja:
                cur.execute(
                    """
                    delete from bancarisation.occurrence
                    where projet_id = %s
                      and origine = 'ia'
                      and modifie_le is null
                    """,
                    (projet_id,),
                )
                supprimees = cur.rowcount

            # 1. Trace d'import
            cur.execute(
                """
                insert into bancarisation.extraction_import (
                    projet_id, fichier_nom, fichier_hash, modele_ocr, modele_llm,
                    nb_metadata, nb_actions, nb_echeances, nb_occurrences, nb_non_placables
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    projet_id, fichier_nom, fichier_hash, modele_ocr, modele_llm,
                    1 if dossier else 0, len(actions.actions),
                    len(echeances), len(occurrences), nb_non_placables,
                ),
            )
            import_id = cur.fetchone()["id"]

            # 2. Métadonnées dossier
            if dossier:
                meta = dossier.dossier
                h = meta.horizon
                cur.execute(
                    """
                    insert into bancarisation.projet_metadata (
                        projet_id, import_id, nom_operation, maitre_ouvrage, operateur,
                        communes, arrete_numero, arrete_date,
                        horizon_debut, horizon_fin, horizon_duree_ans,
                        metadata_json, confiance, champs_a_confirmer, avertissements
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (projet_id) do update set
                        import_id = excluded.import_id,
                        nom_operation = excluded.nom_operation,
                        maitre_ouvrage = excluded.maitre_ouvrage,
                        operateur = excluded.operateur,
                        communes = excluded.communes,
                        arrete_numero = excluded.arrete_numero,
                        arrete_date = excluded.arrete_date,
                        horizon_debut = excluded.horizon_debut,
                        horizon_fin = excluded.horizon_fin,
                        horizon_duree_ans = excluded.horizon_duree_ans,
                        metadata_json = excluded.metadata_json,
                        confiance = excluded.confiance,
                        champs_a_confirmer = excluded.champs_a_confirmer,
                        avertissements = excluded.avertissements,
                        updated_at = now()
                    """,
                    (
                        projet_id, import_id, meta.nom_operation, meta.maitre_ouvrage,
                        meta.operateur, meta.communes, meta.arrete_numero, meta.arrete_date,
                        h.annee_debut, h.annee_fin, h.duree_ans,
                        Jsonb(meta.model_dump(mode="json")),
                        meta.confiance, meta.champs_a_confirmer, meta.avertissements,
                    ),
                )

            # 3. Fiches-actions
            actions_upsert = 0
            for a in actions.actions:
                cur.execute(
                    """
                    insert into bancarisation.action_fiche (
                        projet_id, import_id, cle, code, categorie, titre,
                        contenu_integral, fiche_json, confiance,
                        champs_a_confirmer, avertissements
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (projet_id, cle) do update set
                        import_id = excluded.import_id,
                        code = excluded.code,
                        categorie = excluded.categorie,
                        titre = excluded.titre,
                        contenu_integral = excluded.contenu_integral,
                        fiche_json = excluded.fiche_json,
                        confiance = excluded.confiance,
                        champs_a_confirmer = excluded.champs_a_confirmer,
                        avertissements = excluded.avertissements
                    """,
                    (
                        projet_id, import_id, a.id, a.code, a.categorie, a.titre,
                        a.contenu_integral, Jsonb(a.model_dump(mode="json")),
                        a.confiance, a.champs_a_confirmer, a.avertissements,
                    ),
                )
                actions_upsert += 1

            # 4. Échéances (templates + lien action)
            cle_vers_id: dict[str, UUID] = {}
            for e in echeances:
                cur.execute(
                    """
                    insert into bancarisation.echeance (
                        projet_id, import_id, cle, action_cle, code_operation, type_operation,
                        type_metier, libelle, objectif_long_terme, objectif_operationnel,
                        unites_gestion, parcelles, communes, recurrence,
                        fenetre_debut, fenetre_fin, fenetre_traverse_nouvel_an,
                        fenetre_texte_source, conditions, indicateurs, intervenants,
                        duree_gestion_ans, source_page, source_extrait,
                        confiance, champs_a_confirmer, avertissements
                    ) values (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    on conflict (projet_id, cle) do update set
                        import_id = excluded.import_id,
                        action_cle = excluded.action_cle,
                        libelle = excluded.libelle,
                        recurrence = excluded.recurrence,
                        confiance = excluded.confiance,
                        champs_a_confirmer = excluded.champs_a_confirmer,
                        avertissements = excluded.avertissements
                    returning id
                    """,
                    (
                        projet_id, import_id, e.id, e.action_id, e.code_operation,
                        e.type_operation, e.type_metier, e.libelle,
                        e.objectif_long_terme, e.objectif_operationnel,
                        e.unites_gestion, e.parcelles, e.communes,
                        Jsonb(e.recurrence.model_dump(mode="json")),
                        e.fenetre_intervention.debut, e.fenetre_intervention.fin,
                        e.fenetre_intervention.traverse_nouvel_an,
                        e.fenetre_intervention.texte_source,
                        e.conditions, e.indicateurs, e.intervenants,
                        e.duree_gestion_ans, e.source.page, e.source.extrait,
                        e.confiance, e.champs_a_confirmer, e.avertissements,
                    ),
                )
                cle_vers_id[e.id] = cur.fetchone()["id"]

            # 5. Occurrences calendrier
            inserees = 0
            for o in occurrences:
                echeance_id = cle_vers_id.get(o.echeance_cle) if o.echeance_cle else None
                cur.execute(
                    """
                    insert into bancarisation.occurrence (
                        projet_id, echeance_id, annee, code, titre, categorie, statut,
                        ug_ids, mois_debut, mois_fin, traverse_nouvel_an, origine,
                        confiance, champs_a_confirmer, avertissements
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (echeance_id, annee)
                        where origine = 'ia' and echeance_id is not null
                    do nothing
                    """,
                    (
                        projet_id, echeance_id, o.annee, o.code, o.titre, o.categorie,
                        o.statut, o.ug_ids, o.mois_debut, o.mois_fin, o.traverse_nouvel_an,
                        o.origine, o.confiance, o.champs_a_confirmer, o.avertissements,
                    ),
                )
                inserees += cur.rowcount

            return {
                "import_id": import_id,
                "projet_id": str(projet_id),
                "metadata": 1 if dossier else 0,
                "actions": actions_upsert,
                "echeances": len(cle_vers_id),
                "occurrences_inserees": inserees,
                "occurrences_ia_supprimees": supprimees,
                "occurrences_ignorees": len(occurrences) - inserees,
            }


def charger_occurrences(chemin: Path) -> tuple[list[Occurrence], int]:
    """Lit occurrences.json (format pipeline) ou liste brute."""
    data = json.loads(chemin.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        nb_non = data.get("nb_non_placables", 0)
        raw = data.get("occurrences", [])
    else:
        nb_non = 0
        raw = data
    return [Occurrence.model_validate(o) for o in raw], nb_non


def main() -> None:
    p = argparse.ArgumentParser(description="Ingère les JSON d'extraction en base.")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--projet", help="UUID du projet existant (bancarisation.projets.id)")
    grp.add_argument("--creer-projet", action="store_true",
                     help="Crée un nouveau projet depuis dossier.json puis ingère")
    p.add_argument("--nom-projet", default=None, help="Nom si --creer-projet (sinon dossier.json)")
    p.add_argument("--dossier", default=str(DEFAULT_DOSSIER))
    p.add_argument("--actions", default=str(DEFAULT_ACTIONS))
    p.add_argument("--echeances", default=str(DEFAULT_ECHEANCES),
                   help="echeances_liees.json (échéances + liaisons actions)")
    p.add_argument("--occurrences", default=str(DEFAULT_OCCURRENCES))
    p.add_argument("--markdown", default=None, help="Pour calculer fichier_hash")
    p.add_argument("--modele-ocr", default="mistral-ocr-latest")
    p.add_argument("--modele-llm", default=None)
    p.add_argument("--replace", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    dossier_path = Path(args.dossier)
    actions_path = Path(args.actions)
    echeances_path = Path(args.echeances)
    occ_path = Path(args.occurrences)

    for label, path in [("Actions", actions_path), ("Échéances", echeances_path), ("Occurrences", occ_path)]:
        if not path.exists():
            raise SystemExit(f"❌ {label} introuvable : {path}")

    dossier_result: DossierResult | None = None
    if dossier_path.exists():
        dossier_result = DossierResult.model_validate_json(
            dossier_path.read_text(encoding="utf-8")
        )

    actions_result = ActionsResult.model_validate_json(
        actions_path.read_text(encoding="utf-8")
    )

    # echeances_liees.json ou echeances brutes
    echeances_raw = json.loads(echeances_path.read_text(encoding="utf-8"))
    if "echeances" in echeances_raw and "liaisons" in echeances_raw:
        echeances_liees = EcheancesLieesResult.model_validate(echeances_raw)
    else:
        extraction = ExtractionResult.model_validate(echeances_raw)
        from lier_echeances_actions import lier
        echeances_liees = lier(extraction, actions_result)

    occs, nb_non_placables = charger_occurrences(occ_path)

    fichier_nom = dossier_path.name if dossier_path.exists() else actions_path.name
    fichier_hash = None
    if args.markdown:
        md = Path(args.markdown)
        if md.exists():
            fichier_hash = hashlib.sha256(md.read_text(encoding="utf-8").encode()).hexdigest()
            fichier_nom = md.name

    print(f"📦 Projet {'(à créer)' if args.creer_projet else args.projet}")
    print(f"   métadonnées : {'oui' if dossier_result else 'non'}")
    print(f"   actions     : {len(actions_result.actions)}")
    print(f"   échéances   : {len(echeances_liees.echeances)}")
    print(f"   occurrences : {len(occs)}")

    if args.dry_run:
        print("\n🔍 dry-run — rien écrit en base.")
        return

    with connect() as conn:
        projet_id = resoudre_projet_id(
            conn,
            projet_id=args.projet,
            creer=args.creer_projet,
            dossier=dossier_result,
            nom=args.nom_projet,
        )
        if args.creer_projet:
            print(f"🆕 Projet créé : {projet_id}")
        recap = ingérer(
            conn,
            projet_id,
            dossier=dossier_result,
            actions=actions_result,
            echeances_liees=echeances_liees,
            occurrences=occs,
            fichier_nom=fichier_nom,
            fichier_hash=fichier_hash,
            modele_ocr=args.modele_ocr,
            modele_llm=args.modele_llm,
            nb_non_placables=nb_non_placables,
            replace=args.replace,
        )

    print("\n✅ Ingestion terminée :")
    for k, v in recap.items():
        print(f"   {k:<28} {v}")
    print("\n   → Calendrier : bancarisation.v_occurrence_calendrier")


if __name__ == "__main__":
    main()
