"""CRUD couche contrôle DREAL — lit les tables/vues 023."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from api.db.env import get_database_url
from api.controle.schemas import (
    ActeDrealOut,
    ArreteOut,
    ArretePrescriptionOut,
    BilanSuiviOut,
    ConstatControleOut,
    DossierControleOut,
    ItemBannetteOut,
    LigneConformiteOut,
    ActionFicheOption,
)

def _conn():
    return psycopg.connect(get_database_url(), row_factory=dict_row)


def _map_conformite_ui(raw: str | None) -> str:
    if raw == "reserve":
        return "ecart"
    if raw == "non_evalue" or raw is None:
        return "non_controlee"
    return raw


def _iso_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _rubriques_from_extraction(extraction: Any) -> list[str]:
    if not extraction:
        return []
    if isinstance(extraction, str):
        try:
            extraction = json.loads(extraction)
        except json.JSONDecodeError:
            return []
    if not isinstance(extraction, dict):
        return []
    meta = extraction.get("metadonnees") or {}
    rub = meta.get("rubriques") or []
    if isinstance(rub, list):
        return [str(x) for x in rub if x]
    return []


def _map_prescription_row(r: dict[str, Any]) -> ArretePrescriptionOut:
    rec = r.get("recurrence")
    if isinstance(rec, str):
        try:
            rec = json.loads(rec)
        except json.JSONDecodeError:
            rec = None
    return ArretePrescriptionOut(
        id=r.get("id"),
        article=r.get("article"),
        intitule=r.get("intitule") or "",
        categorie=r.get("categorie"),
        nature=r.get("nature"),
        cible_valeur=float(r["cible_valeur"]) if r.get("cible_valeur") is not None else None,
        cible_unite=r.get("cible_unite"),
        echeance=_iso_date(r.get("echeance")),
        recurrence=rec if isinstance(rec, dict) else None,
        page_source=r.get("page_source"),
        texte_source=r.get("texte_source"),
        confiance=float(r["confiance"]) if r.get("confiance") is not None else None,
    )


def _map_arrete_row(r: dict[str, Any], prescriptions: list[ArretePrescriptionOut] | None = None) -> ArreteOut:
    return ArreteOut(
        id=r["id"],
        projet_id=r["projet_id"],
        type=r["type"],
        reference=r.get("reference"),
        date_notification=_iso_date(r.get("date_notification")),
        date_signature=_iso_date(r.get("date_signature")),
        document_id=r.get("document_id"),
        autorite=r.get("autorite"),
        beneficiaire=r.get("beneficiaire"),
        numero_dossier=r.get("numero_dossier"),
        confiance=float(r["confiance"]) if r.get("confiance") is not None else None,
        extraction_modele=r.get("extraction_modele"),
        rubriques=_rubriques_from_extraction(r.get("extraction")),
        prescriptions=prescriptions or [],
    )


def _charger_prescriptions(cur, arrete_ids: list[str]) -> dict[str, list[ArretePrescriptionOut]]:
    if not arrete_ids:
        return {}
    cur.execute(
        """
        SELECT id, arrete_id, article, intitule, categorie, nature,
               cible_valeur, cible_unite, echeance, recurrence,
               page_source, texte_source, confiance
        FROM bancarisation.arrete_prescription
        WHERE arrete_id = ANY(%s::uuid[])
        ORDER BY article NULLS LAST, cree_le
        """,
        (arrete_ids,),
    )
    by: dict[str, list[ArretePrescriptionOut]] = {aid: [] for aid in arrete_ids}
    for row in cur.fetchall():
        aid = str(row["arrete_id"])
        by.setdefault(aid, []).append(_map_prescription_row(row))
    return by


def lister_bannette(
    *,
    role: str,
    organisation_id: UUID | None,
) -> list[ItemBannetteOut]:
    if role in ("controleur", "admin"):
        where = "TRUE"
        params: list[Any] = []
    elif organisation_id is None:
        return []
    else:
        where = "p.organisation_id = %s"
        params = [str(organisation_id)]

    sql = f"""
        SELECT
          b.id, b.projet_id, b.projet_nom, b.organisation_nom,
          b.motif, b.libelle, b.echeance, b.priorite,
          b.statut_controle, b.bilan_id, b.acte_id,
          coalesce(vp.gravite, 0) AS gravite
        FROM bancarisation.v_bannette_a_traiter b
        JOIN bancarisation.projets p ON p.id = b.projet_id
        LEFT JOIN bancarisation.v_parc_projet vp ON vp.projet_id = b.projet_id
        WHERE {where}
        ORDER BY b.priorite DESC, b.echeance ASC NULLS LAST
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    out: list[ItemBannetteOut] = []
    for r in rows:
        out.append(
            ItemBannetteOut(
                id=str(r["id"]),
                projet_id=r["projet_id"],
                projet_nom=r["projet_nom"],
                organisation_nom=r["organisation_nom"],
                motif=r["motif"],
                libelle=r["libelle"] or "",
                echeance=_iso_date(r.get("echeance")),
                priorite=int(r.get("priorite") or 0),
                statut_controle=r["statut_controle"],
                gravite=int(r.get("gravite") or 0),
                acte_id=r.get("acte_id"),
                bilan_id=r.get("bilan_id"),
            )
        )
    return out


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


def lire_dossier(
    projet_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> DossierControleOut:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )

            cur.execute(
                """
                SELECT statut_controle, force_manuel
                FROM bancarisation.v_projet_statut_effectif
                WHERE projet_id = %s
                """,
                (str(projet_id),),
            )
            st = cur.fetchone() or {"statut_controle": "en_suivi", "force_manuel": False}

            cur.execute(
                """
                SELECT statut_controle_override
                FROM bancarisation.projets WHERE id = %s
                """,
                (str(projet_id),),
            )
            ov = cur.fetchone()
            override = ov.get("statut_controle_override") if ov else None

            cur.execute(
                """
                SELECT id, projet_id, type, reference, date_notification,
                       date_signature, document_id, autorite, beneficiaire,
                       numero_dossier, confiance, extraction_modele, extraction
                FROM bancarisation.arrete
                WHERE projet_id = %s
                ORDER BY date_notification NULLS LAST, cree_le
                """,
                (str(projet_id),),
            )
            arrete_rows = cur.fetchall()
            presc_by = _charger_prescriptions(
                cur, [str(r["id"]) for r in arrete_rows]
            )
            arretes = [
                _map_arrete_row(r, presc_by.get(str(r["id"]), []))
                for r in arrete_rows
            ]

            # Lignes conformité = action_fiche + prescriptions + vue conformité
            cur.execute(
                """
                SELECT
                  af.id AS action_fiche_id,
                  af.code,
                  af.titre,
                  c.conformite,
                  c.nb_constats,
                  (
                    SELECT json_agg(json_build_object(
                      'intitule', p.intitule,
                      'cible_valeur', p.cible_valeur,
                      'cible_unite', p.cible_unite,
                      'echeance', p.echeance,
                      'article', p.article
                    ) ORDER BY p.article NULLS LAST)
                    FROM bancarisation.arrete_prescription p
                    WHERE p.action_fiche_id = af.id
                  ) AS prescriptions,
                  (
                    SELECT o.statut
                    FROM bancarisation.v_occurrence_action_fiche v
                    JOIN bancarisation.occurrence o ON o.id = v.occurrence_id
                    WHERE v.action_fiche_id = af.id
                    ORDER BY o.annee DESC NULLS LAST
                    LIMIT 1
                  ) AS statut_execution,
                  (
                    SELECT o.date_realisation
                    FROM bancarisation.v_occurrence_action_fiche v
                    JOIN bancarisation.occurrence o ON o.id = v.occurrence_id
                    WHERE v.action_fiche_id = af.id
                      AND o.date_realisation IS NOT NULL
                    ORDER BY o.date_realisation DESC
                    LIMIT 1
                  ) AS realise_le
                FROM bancarisation.action_fiche af
                LEFT JOIN bancarisation.v_action_fiche_conformite c
                  ON c.action_fiche_id = af.id
                WHERE af.projet_id = %s
                ORDER BY af.code
                """,
                (str(projet_id),),
            )
            lignes: list[LigneConformiteOut] = []
            for r in cur.fetchall():
                presc_list = r.get("prescriptions") or []
                if isinstance(presc_list, str):
                    presc_list = json.loads(presc_list)
                first = presc_list[0] if presc_list else {}
                surface = None
                unite = (first or {}).get("cible_unite")
                val = (first or {}).get("cible_valeur")
                if val is not None and unite in ("ha", "m2", "m²"):
                    surface = float(val) / 10000.0 if unite in ("m2", "m²") else float(val)
                prescrit = {
                    "surface_ha": surface,
                    "moyen": (first or {}).get("intitule"),
                    "echeance": str((first or {}).get("echeance") or "")[:10] or None,
                    "resultat_attendu": (first or {}).get("intitule"),
                }
                conf = _map_conformite_ui(r.get("conformite"))
                ecart = None
                if conf == "ecart":
                    ecart = "Réserve constatée — voir constats"
                elif conf == "non_conforme":
                    ecart = "Non-conformité constatée — voir constats"
                lignes.append(
                    LigneConformiteOut(
                        mesure_id=r["action_fiche_id"],
                        code=r["code"] or "",
                        libelle=r["titre"] or "",
                        prescrit=prescrit,
                        constate={
                            "surface_ha": None,
                            "moyen": None,
                            "realise_le": str(r["realise_le"])[:10] if r.get("realise_le") else None,
                            "resultat": None,
                        },
                        conformite=conf,
                        ecart_explicite=ecart,
                        statut_execution=r.get("statut_execution"),
                        nb_constats=int(r.get("nb_constats") or 0),
                    )
                )

            cur.execute(
                """
                SELECT id, projet_id, annee, statut, depose_le, statue_le, document_id
                FROM bancarisation.bilan_suivi
                WHERE projet_id = %s
                ORDER BY annee DESC
                """,
                (str(projet_id),),
            )
            bilans = [
                BilanSuiviOut(
                    id=r["id"],
                    projet_id=r["projet_id"],
                    annee=int(r["annee"]),
                    statut=r["statut"],
                    depose_le=r.get("depose_le"),
                    statue_le=r.get("statue_le"),
                    document_id=r.get("document_id"),
                )
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT id, projet_id, type, statut, objet, contenu,
                       delai_reponse, emis_le, document_id, demande_id, cree_le
                FROM bancarisation.acte_dreal
                WHERE projet_id = %s
                ORDER BY cree_le DESC
                """,
                (str(projet_id),),
            )
            actes: list[ActeDrealOut] = []
            for r in cur.fetchall():
                contenu = r.get("contenu") or {}
                if isinstance(contenu, str):
                    contenu = json.loads(contenu)
                actes.append(
                    ActeDrealOut(
                        id=r["id"],
                        projet_id=r["projet_id"],
                        type=r["type"],
                        statut=r["statut"],
                        objet=r.get("objet"),
                        contenu=contenu if isinstance(contenu, dict) else {},
                        delai_reponse=_iso_date(r.get("delai_reponse")),
                        emis_le=r.get("emis_le"),
                        document_id=r.get("document_id"),
                        demande_id=r.get("demande_id"),
                        cree_le=r["cree_le"],
                    )
                )

            cur.execute(
                """
                SELECT c.id, c.action_fiche_id, c.occurrence_id, c.date_constat,
                       c.auteur, c.mode, c.conformite, c.commentaire, c.documents,
                       coalesce(af.projet_id, o.projet_id) AS projet_id
                FROM bancarisation.constat_controle c
                LEFT JOIN bancarisation.action_fiche af ON af.id = c.action_fiche_id
                LEFT JOIN bancarisation.occurrence o ON o.id = c.occurrence_id
                WHERE af.projet_id = %s OR o.projet_id = %s
                ORDER BY c.date_constat DESC
                """,
                (str(projet_id), str(projet_id)),
            )
            constats: list[ConstatControleOut] = []
            for r in cur.fetchall():
                docs = r.get("documents") or []
                if isinstance(docs, str):
                    docs = json.loads(docs)
                if not isinstance(docs, list):
                    docs = []
                pieces = [str(x) for x in docs]
                constats.append(
                    ConstatControleOut(
                        id=r["id"],
                        mesure_id=r.get("action_fiche_id"),
                        occurrence_id=r.get("occurrence_id"),
                        projet_id=r["projet_id"],
                        date=_iso_date(r["date_constat"]) or date.today(),
                        auteur=r.get("auteur"),
                        mode=r["mode"],
                        conformite=r["conformite"],
                        commentaire=r.get("commentaire"),
                        pieces=pieces,
                    )
                )

    return DossierControleOut(
        projet_id=projet_id,
        statut_controle=st["statut_controle"],
        statut_controle_override=override,
        arretes=arretes,
        lignes_conformite=lignes,
        bilans=bilans,
        actes=actes,
        constats=constats,
    )


def patch_bilan_statut(
    projet_id: UUID,
    bilan_id: UUID,
    statut: str,
    *,
    role: str,
    organisation_id: UUID | None,
) -> BilanSuiviOut:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            statue = statut in ("valide", "rejete", "complement_demande")
            cur.execute(
                """
                UPDATE bancarisation.bilan_suivi
                SET statut = %s,
                    statue_le = CASE WHEN %s THEN now() ELSE statue_le END,
                    depose_le = CASE
                      WHEN %s IN ('depose','en_relecture') AND depose_le IS NULL
                      THEN now() ELSE depose_le END
                WHERE id = %s AND projet_id = %s
                RETURNING id, projet_id, annee, statut, depose_le, statue_le, document_id
                """,
                (statut, statue, statut, str(bilan_id), str(projet_id)),
            )
            r = cur.fetchone()
            if not r:
                raise LookupError("Bilan introuvable")
            conn.commit()
            return BilanSuiviOut(
                id=r["id"],
                projet_id=r["projet_id"],
                annee=int(r["annee"]),
                statut=r["statut"],
                depose_le=r.get("depose_le"),
                statue_le=r.get("statue_le"),
                document_id=r.get("document_id"),
            )


def emettre_acte(
    projet_id: UUID,
    acte_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> ActeDrealOut:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            cur.execute(
                """
                UPDATE bancarisation.acte_dreal
                SET statut = 'emis',
                    emis_le = now(),
                    delai_reponse = coalesce(
                      delai_reponse,
                      (current_date + (coalesce(nullif(contenu->>'delai_jours','')::int, 30) * interval '1 day'))::date
                    )
                WHERE id = %s AND projet_id = %s
                RETURNING id, projet_id, type, statut, objet, contenu,
                          delai_reponse, emis_le, document_id, demande_id, cree_le
                """,
                (str(acte_id), str(projet_id)),
            )
            r = cur.fetchone()
            if not r:
                raise LookupError("Acte introuvable")
            conn.commit()
            contenu = r.get("contenu") or {}
            if isinstance(contenu, str):
                contenu = json.loads(contenu)
            return ActeDrealOut(
                id=r["id"],
                projet_id=r["projet_id"],
                type=r["type"],
                statut=r["statut"],
                objet=r.get("objet"),
                contenu=contenu if isinstance(contenu, dict) else {},
                delai_reponse=_iso_date(r.get("delai_reponse")),
                emis_le=r.get("emis_le"),
                document_id=r.get("document_id"),
                demande_id=r.get("demande_id"),
                cree_le=r["cree_le"],
            )


def generer_acte_depuis_ecarts(
    projet_id: UUID,
    *,
    type_acte: str,
    delai_jours: int,
    role: str,
    organisation_id: UUID | None,
) -> ActeDrealOut:
    dossier = lire_dossier(projet_id, role=role, organisation_id=organisation_id)
    ecarts = [
        l
        for l in dossier.lignes_conformite
        if l.conformite in ("ecart", "non_conforme")
    ]
    if not ecarts:
        raise ValueError("Aucun écart de conformité à formaliser")

    contenu = {
        "mesures_visees": [e.code for e in ecarts],
        "ecarts": [e.ecart_explicite or e.libelle for e in ecarts],
        "delai_jours": delai_jours,
        "corps": "Vu les écarts constatés :\n"
        + "\n".join(f"• {e.code} — {e.ecart_explicite or e.libelle}" for e in ecarts),
    }
    objet = (
        "Demande de compléments — écarts de conformité"
        if type_acte == "demande_complement"
        else "Projet de mise en demeure — écarts de conformité"
    )

    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            cur.execute(
                """
                INSERT INTO bancarisation.acte_dreal
                  (projet_id, type, statut, objet, contenu)
                VALUES (%s, %s, 'brouillon', %s, %s)
                RETURNING id, projet_id, type, statut, objet, contenu,
                          delai_reponse, emis_le, document_id, demande_id, cree_le
                """,
                (str(projet_id), type_acte, objet, Json(contenu)),
            )
            r = cur.fetchone()
            conn.commit()
            c = r.get("contenu") or {}
            if isinstance(c, str):
                c = json.loads(c)
            return ActeDrealOut(
                id=r["id"],
                projet_id=r["projet_id"],
                type=r["type"],
                statut=r["statut"],
                objet=r.get("objet"),
                contenu=c if isinstance(c, dict) else {},
                delai_reponse=_iso_date(r.get("delai_reponse")),
                emis_le=r.get("emis_le"),
                document_id=r.get("document_id"),
                demande_id=r.get("demande_id"),
                cree_le=r["cree_le"],
            )


def lister_statuts(
    *,
    role: str,
    organisation_id: UUID | None,
) -> list[dict[str, Any]]:
    if role in ("controleur", "admin"):
        where = "TRUE"
        params: list[Any] = []
    elif organisation_id is None:
        return []
    else:
        where = "p.organisation_id = %s"
        params = [str(organisation_id)]
    sql = f"""
        SELECT s.projet_id, s.statut_controle, s.force_manuel
        FROM bancarisation.v_projet_statut_effectif s
        JOIN bancarisation.projets p ON p.id = s.projet_id
        WHERE {where}
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def lister_actions_fiche(
    projet_id: UUID,
    *,
    role: str,
    organisation_id: UUID | None,
) -> list[ActionFicheOption]:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            cur.execute(
                """
                SELECT id, code, titre
                FROM bancarisation.action_fiche
                WHERE projet_id = %s
                ORDER BY code
                """,
                (str(projet_id),),
            )
            return [
                ActionFicheOption(id=r["id"], code=r["code"], titre=r["titre"])
                for r in cur.fetchall()
            ]


def creer_arrete(
    projet_id: UUID,
    *,
    type_arrete: str,
    reference: str | None,
    date_notification: date | None,
    document_id: UUID | None,
    action_fiche_ids: list[UUID] | None,
    role: str,
    organisation_id: UUID | None,
    origine: str = "user",
) -> ArreteOut:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            cur.execute(
                """
                INSERT INTO bancarisation.arrete
                  (projet_id, type, reference, date_notification, document_id, origine)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, projet_id, type, reference, date_notification,
                          date_signature, document_id, autorite, beneficiaire,
                          numero_dossier, confiance, extraction_modele, extraction
                """,
                (
                    str(projet_id),
                    type_arrete,
                    reference,
                    date_notification,
                    str(document_id) if document_id else None,
                    origine,
                ),
            )
            r = cur.fetchone()
            arrete_id = r["id"]
            for af_id in action_fiche_ids or []:
                cur.execute(
                    """
                    INSERT INTO bancarisation.action_fiche_arrete (action_fiche_id, arrete_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (str(af_id), str(arrete_id)),
                )
            conn.commit()
            return _map_arrete_row(r, [])


def lire_arrete(
    arrete_id: UUID,
    *,
    projet_id: UUID,
    role: str,
    organisation_id: UUID | None,
) -> ArreteOut:
    with _conn() as conn:
        with conn.cursor() as cur:
            _assert_projet_accessible(
                cur, projet_id, role=role, organisation_id=organisation_id
            )
            cur.execute(
                """
                SELECT id, projet_id, type, reference, date_notification,
                       date_signature, document_id, autorite, beneficiaire,
                       numero_dossier, confiance, extraction_modele, extraction
                FROM bancarisation.arrete
                WHERE id = %s AND projet_id = %s
                """,
                (str(arrete_id), str(projet_id)),
            )
            r = cur.fetchone()
            if not r:
                raise LookupError("Arrêté introuvable")
            presc = _charger_prescriptions(cur, [str(r["id"])]).get(str(r["id"]), [])
            return _map_arrete_row(r, presc)


def persister_extraction_arrete(
    arrete_id: UUID,
    *,
    arrete_row: dict[str, Any],
    prescriptions: list[dict[str, Any]],
) -> ArreteOut:
    """Met à jour l'arrêté avec les champs IA et remplace les prescriptions."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bancarisation.arrete SET
                  type = coalesce(%s, type),
                  reference = coalesce(%s, reference),
                  autorite = %s,
                  beneficiaire = %s,
                  numero_dossier = %s,
                  date_signature = %s::date,
                  date_notification = coalesce(%s::date, date_notification),
                  extraction = %s,
                  extraction_modele = %s,
                  confiance = %s,
                  origine = 'ia',
                  modifie_le = now()
                WHERE id = %s
                RETURNING id, projet_id, type, reference, date_notification,
                          date_signature, document_id, autorite, beneficiaire,
                          numero_dossier, confiance, extraction_modele, extraction
                """,
                (
                    arrete_row.get("type"),
                    arrete_row.get("reference"),
                    arrete_row.get("autorite"),
                    arrete_row.get("beneficiaire"),
                    arrete_row.get("numero_dossier"),
                    arrete_row.get("date_signature"),
                    arrete_row.get("date_notification"),
                    Json(arrete_row.get("extraction")) if arrete_row.get("extraction") is not None else None,
                    arrete_row.get("extraction_modele"),
                    arrete_row.get("confiance"),
                    str(arrete_id),
                ),
            )
            r = cur.fetchone()
            if not r:
                raise LookupError("Arrêté introuvable pour persistance extraction")

            cur.execute(
                "DELETE FROM bancarisation.arrete_prescription WHERE arrete_id = %s",
                (str(arrete_id),),
            )
            for p in prescriptions:
                cat = p.get("categorie")
                nat = p.get("nature")
                if cat not in (
                    "compensation",
                    "evitement",
                    "reduction",
                    "accompagnement",
                    "suivi",
                    "chantier",
                    "administratif",
                ):
                    cat = None
                if nat not in (
                    "calendaire",
                    "recurrente",
                    "permanente",
                    "ponctuelle",
                    "seuil",
                ):
                    nat = None
                cur.execute(
                    """
                    INSERT INTO bancarisation.arrete_prescription (
                      arrete_id, article, intitule, categorie, nature,
                      cible_valeur, cible_unite, echeance, recurrence,
                      page_source, texte_source, confiance, origine
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s::date, %s, %s, %s, %s, 'ia'
                    )
                    """,
                    (
                        str(arrete_id),
                        p.get("article"),
                        p.get("intitule") or "Prescription",
                        cat,
                        nat,
                        p.get("cible_valeur"),
                        p.get("cible_unite"),
                        p.get("echeance"),
                        Json(p["recurrence"]) if p.get("recurrence") is not None else None,
                        p.get("page_source"),
                        p.get("texte_source"),
                        p.get("confiance"),
                    ),
                )
            conn.commit()
            presc = _charger_prescriptions(cur, [str(arrete_id)]).get(str(arrete_id), [])
            return _map_arrete_row(r, presc)
