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


CATEGORIES_OK = {
    "compensation",
    "evitement",
    "reduction",
    "accompagnement",
    "suivi",
    "chantier",
    "administratif",
    "gouvernance",
    "information",
    "donnees",
}
NATURES_OK = {
    "calendaire",
    "recurrente",
    "permanente",
    "ponctuelle",
    "seuil",
}


def _as_dict(v: Any) -> dict[str, Any] | None:
    if v is None:
        return None
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _as_str_list(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return [v] if v else []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return []


def _extraction_dict(extraction: Any) -> dict[str, Any]:
    data = _as_dict(extraction) or {}
    meta = data.get("metadonnees")
    if isinstance(meta, dict):
        fused = {**meta, **{k: v for k, v in data.items() if k != "metadonnees"}}
        return fused
    return data


def _rubriques_from_extraction(extraction: Any) -> list[str]:
    data = _extraction_dict(extraction)
    rub = data.get("rubriques") or []
    if not isinstance(rub, list):
        return []
    out: list[str] = []
    for x in rub:
        if isinstance(x, dict):
            label = x.get("rubrique") or x.get("intitule_court")
            if label:
                out.append(str(label))
        elif x:
            out.append(str(x))
    return out


def _map_prescription_row(r: dict[str, Any]) -> ArretePrescriptionOut:
    rec = _as_dict(r.get("recurrence"))
    tempo = _as_dict(r.get("temporalite"))
    dest = _as_str_list(r.get("autorite_destinataire"))
    opp = r.get("opposable")
    if opp is None:
        opp = True
    return ArretePrescriptionOut(
        id=r.get("id"),
        article=r.get("article"),
        intitule=r.get("intitule") or "",
        categorie=r.get("categorie"),
        nature=r.get("nature"),
        cible_valeur=float(r["cible_valeur"]) if r.get("cible_valeur") is not None else None,
        cible_unite=r.get("cible_unite"),
        echeance=_iso_date(r.get("echeance")),
        recurrence=rec,
        page_source=r.get("page_source"),
        texte_source=r.get("texte_source"),
        confiance=float(r["confiance"]) if r.get("confiance") is not None else None,
        code=r.get("code"),
        opposable=bool(opp),
        phase=r.get("phase"),
        destinataire=r.get("destinataire"),
        livrable=r.get("livrable"),
        indicateur=r.get("indicateur"),
        obligation_de_resultat=bool(r.get("obligation_de_resultat") or False),
        temporalite=tempo,
        autorite_destinataire=dest,
        remarque=r.get("remarque"),
    )


def _map_arrete_row(r: dict[str, Any], prescriptions: list[ArretePrescriptionOut] | None = None) -> ArreteOut:
    ext = _extraction_dict(r.get("extraction"))
    site = ext.get("site_compensation")
    fondement = ext.get("fondement") or []
    if not isinstance(fondement, list):
        fondement = [fondement] if fondement else []
    avert = ext.get("avertissements") or []
    if not isinstance(avert, list):
        avert = [str(avert)] if avert else []
    duree = ext.get("duree_suivi_annees")
    try:
        duree_i = int(duree) if duree is not None else None
    except (TypeError, ValueError):
        duree_i = None
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
        service_instructeur=ext.get("service_instructeur") or None,
        fondement=[str(x) for x in fondement if x],
        projet_nom=ext.get("projet_nom") or None,
        duree_suivi_annees=duree_i,
        avertissements=[str(x) for x in avert if x],
        site_compensation=site if isinstance(site, dict) else None,
    )


def _charger_prescriptions(cur, arrete_ids: list[str]) -> dict[str, list[ArretePrescriptionOut]]:
    if not arrete_ids:
        return {}
    cur.execute(
        """
        SELECT *
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


def _resume_justification_controles(controles: Any) -> tuple[int, int, str | None]:
    """Extrait les manques de preuve / commentaire depuis rapport_suivi.controles."""
    if isinstance(controles, str):
        try:
            controles = json.loads(controles)
        except json.JSONDecodeError:
            controles = []
    if not isinstance(controles, list):
        return 0, 0, None

    nb_sans_preuve = 0
    nb_sans_commentaire = 0
    for c in controles:
        if not isinstance(c, dict):
            continue
        code = c.get("code")
        occs = c.get("occurrences") or []
        n = len(occs) if isinstance(occs, list) else 0
        if code == "realise_sans_preuve":
            nb_sans_preuve = max(nb_sans_preuve, n)
        elif code == "realise_sans_commentaire":
            nb_sans_commentaire = max(nb_sans_commentaire, n)

    parts: list[str] = []
    if nb_sans_preuve:
        parts.append(
            f"{nb_sans_preuve} sans preuve"
            if nb_sans_preuve > 1
            else "1 sans preuve"
        )
    if nb_sans_commentaire:
        parts.append(
            f"{nb_sans_commentaire} sans précision BE"
            if nb_sans_commentaire > 1
            else "1 sans précision BE"
        )
    detail = " · ".join(parts) if parts else None
    return nb_sans_preuve, nb_sans_commentaire, detail


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
          coalesce(vp.gravite, 0) AS gravite,
          rs.controles AS rapport_controles
        FROM bancarisation.v_bannette_a_traiter b
        JOIN bancarisation.projets p ON p.id = b.projet_id
        LEFT JOIN bancarisation.v_parc_projet vp ON vp.projet_id = b.projet_id
        LEFT JOIN bancarisation.bilan_suivi bs ON bs.id = b.bilan_id
        LEFT JOIN bancarisation.rapport_suivi rs
          ON rs.id = bs.rapport_suivi_id
          OR (
            bs.rapport_suivi_id IS NULL
            AND rs.bilan_suivi_id = bs.id
          )
        WHERE {where}
        ORDER BY b.priorite DESC, b.echeance ASC NULLS LAST
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    out: list[ItemBannetteOut] = []
    for r in rows:
        nb_sp, nb_sc, detail = (0, 0, None)
        if r.get("motif") == "bilan_a_valider":
            nb_sp, nb_sc, detail = _resume_justification_controles(
                r.get("rapport_controles")
            )
        alerte = bool(nb_sp or nb_sc)
        priorite = int(r.get("priorite") or 0)
        if alerte:
            priorite = max(priorite, 3)
        libelle = r["libelle"] or ""
        if alerte and detail:
            libelle = f"{libelle} — Justifications manquantes : {detail}"

        out.append(
            ItemBannetteOut(
                id=str(r["id"]),
                projet_id=r["projet_id"],
                projet_nom=r["projet_nom"],
                organisation_nom=r["organisation_nom"],
                motif=r["motif"],
                libelle=libelle,
                echeance=_iso_date(r.get("echeance")),
                priorite=priorite,
                statut_controle=r["statut_controle"],
                gravite=int(r.get("gravite") or 0),
                acte_id=r.get("acte_id"),
                bilan_id=r.get("bilan_id"),
                alerte_justification=alerte,
                detail_justification=detail,
                nb_sans_preuve=nb_sp,
                nb_sans_commentaire=nb_sc,
            )
        )
    # Re-tri local si priorités ont été remontées
    out.sort(
        key=lambda x: (
            -x.priorite,
            x.echeance is None,
            x.echeance or date.max,
            -int(x.alerte_justification),
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
                SELECT
                  bs.id, bs.projet_id, bs.annee, bs.statut,
                  bs.depose_le, bs.statue_le,
                  coalesce(bs.document_id, rs.document_id) AS document_id,
                  coalesce(bs.rapport_suivi_id, rs.id) AS rapport_suivi_id,
                  rs.controles AS rapport_controles
                FROM bancarisation.bilan_suivi bs
                LEFT JOIN bancarisation.rapport_suivi rs
                  ON rs.id = bs.rapport_suivi_id
                  OR (
                    bs.rapport_suivi_id IS NULL
                    AND rs.bilan_suivi_id = bs.id
                  )
                WHERE bs.projet_id = %s
                ORDER BY bs.annee DESC
                """,
                (str(projet_id),),
            )
            bilans: list[BilanSuiviOut] = []
            for r in cur.fetchall():
                nb_sp, nb_sc, detail = _resume_justification_controles(
                    r.get("rapport_controles")
                )
                bilans.append(
                    BilanSuiviOut(
                        id=r["id"],
                        projet_id=r["projet_id"],
                        annee=int(r["annee"]),
                        statut=r["statut"],
                        depose_le=r.get("depose_le"),
                        statue_le=r.get("statue_le"),
                        document_id=r.get("document_id"),
                        rapport_suivi_id=r.get("rapport_suivi_id"),
                        alerte_justification=bool(nb_sp or nb_sc),
                        detail_justification=detail,
                        nb_sans_preuve=nb_sp,
                        nb_sans_commentaire=nb_sc,
                    )
                )

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
                RETURNING id, projet_id, annee, statut, depose_le, statue_le,
                          document_id, rapport_suivi_id
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
                rapport_suivi_id=r.get("rapport_suivi_id"),
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


def _colonnes_prescription(cur: Any) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'bancarisation'
          AND table_name = 'arrete_prescription'
        """
    )
    return {r["column_name"] for r in cur.fetchall()}


def _inserer_prescriptions(cur: Any, arrete_id: str, prescriptions: list[dict[str, Any]]) -> None:
    cols = _colonnes_prescription(cur)
    riche = "temporalite" in cols and "code" in cols
    for p in prescriptions:
        cat = p.get("categorie") if p.get("categorie") in CATEGORIES_OK else None
        nat = p.get("nature") if p.get("nature") in NATURES_OK else None
        dest = p.get("autorite_destinataire")
        if dest is not None and not isinstance(dest, (list, dict)):
            dest = [dest]
        if riche:
            cur.execute(
                """
                INSERT INTO bancarisation.arrete_prescription (
                  arrete_id, article, intitule, categorie, nature,
                  cible_valeur, cible_unite, echeance, recurrence,
                  page_source, texte_source, confiance, origine,
                  code, opposable, phase, destinataire, livrable,
                  indicateur, obligation_de_resultat, temporalite,
                  autorite_destinataire, remarque
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s::date, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s
                )
                """,
                (
                    arrete_id,
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
                    p.get("origine") or "ia",
                    p.get("code"),
                    True if p.get("opposable") is None else bool(p.get("opposable")),
                    p.get("phase"),
                    p.get("destinataire"),
                    p.get("livrable"),
                    p.get("indicateur"),
                    bool(p.get("obligation_de_resultat") or False),
                    Json(p["temporalite"]) if p.get("temporalite") is not None else None,
                    Json(dest) if dest is not None else None,
                    p.get("remarque"),
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO bancarisation.arrete_prescription (
                  arrete_id, article, intitule, categorie, nature,
                  cible_valeur, cible_unite, echeance, recurrence,
                  page_source, texte_source, confiance, origine
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s::date, %s, %s, %s, %s, %s
                )
                """,
                (
                    arrete_id,
                    p.get("article"),
                    p.get("intitule") or "Prescription",
                    cat if cat in {
                        "compensation", "evitement", "reduction", "accompagnement",
                        "suivi", "chantier", "administratif",
                    } else None,
                    nat,
                    p.get("cible_valeur"),
                    p.get("cible_unite"),
                    p.get("echeance"),
                    Json(p["recurrence"]) if p.get("recurrence") is not None else None,
                    p.get("page_source"),
                    p.get("texte_source"),
                    p.get("confiance"),
                    p.get("origine") or "ia",
                ),
            )


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
            _inserer_prescriptions(cur, str(arrete_id), prescriptions)
            conn.commit()
            presc = _charger_prescriptions(cur, [str(arrete_id)]).get(str(arrete_id), [])
            return _map_arrete_row(r, presc)


def _document_ids_arrete(cur: Any, projet_id: UUID) -> list[str]:
    """PDF d'arrêté du projet (catégorie ou dossier storage `/arrete/`)."""
    try:
        cur.execute(
            """
            SELECT id
            FROM bancarisation.documents
            WHERE projet_id = %s
              AND (
                categorie = 'arrete'
                OR bucket_path ILIKE '%%/arrete/%%'
              )
            ORDER BY id DESC
            """,
            (str(projet_id),),
        )
    except Exception:  # noqa: BLE001
        return []
    return [str(r["id"]) for r in cur.fetchall() if r.get("id")]


def lister_arretes_projet(projet_id: UUID) -> list[ArreteOut]:
    """Lecture BE — pas de gate DREAL. Même payload que le dossier de contrôle."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, projet_id, type, reference, date_notification,
                       date_signature, document_id, autorite, beneficiaire,
                       numero_dossier, confiance, extraction_modele, extraction
                FROM bancarisation.arrete
                WHERE projet_id = %s
                ORDER BY date_notification NULLS LAST, date_signature NULLS LAST, cree_le
                """,
                (str(projet_id),),
            )
            rows = list(cur.fetchall())
            doc_ids = _document_ids_arrete(cur, projet_id)
            fallback = doc_ids[0] if doc_ids else None
            if fallback:
                for r in rows:
                    if r.get("document_id"):
                        continue
                    r["document_id"] = fallback
                    cur.execute(
                        """
                        UPDATE bancarisation.arrete
                        SET document_id = %s, modifie_le = now()
                        WHERE id = %s AND document_id IS NULL
                        """,
                        (fallback, str(r["id"])),
                    )
                conn.commit()
            presc_by = _charger_prescriptions(cur, [str(r["id"]) for r in rows])
            return [
                _map_arrete_row(r, presc_by.get(str(r["id"]), []))
                for r in rows
            ]


TYPES_ARRETE_OK = {
    "declaration_loi_eau",
    "autorisation_env",
    "derogation_ep",
    "arrete_modificatif",
    "autre",
}


def ingerer_arretes_ia(
    projet_id: UUID,
    lots: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    *,
    replace_ia: bool = True,
) -> dict[str, Any]:
    """INSERT arrêtés extraits par le DAG. Remplace les lignes origine='ia' du projet."""
    if not lots:
        return {"arretes": 0, "prescriptions": 0, "ids": []}

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM bancarisation.projets WHERE id = %s",
                (str(projet_id),),
            )
            if not cur.fetchone():
                raise ValueError(f"Projet introuvable : {projet_id}")

            if replace_ia:
                cur.execute(
                    """
                    DELETE FROM bancarisation.arrete
                    WHERE projet_id = %s AND origine = 'ia'
                    """,
                    (str(projet_id),),
                )

            ids: list[str] = []
            n_presc = 0
            for arrete_row, prescriptions in lots:
                type_a = arrete_row.get("type") or "autre"
                if type_a not in TYPES_ARRETE_OK:
                    type_a = "autre"
                cur.execute(
                    """
                    INSERT INTO bancarisation.arrete (
                      projet_id, type, reference, autorite, beneficiaire,
                      numero_dossier, date_signature, date_notification,
                      document_id, extraction, extraction_modele, confiance, origine
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s::date, %s::date,
                      %s, %s, %s, %s, 'ia'
                    )
                    RETURNING id
                    """,
                    (
                        str(projet_id),
                        type_a,
                        arrete_row.get("reference"),
                        arrete_row.get("autorite"),
                        arrete_row.get("beneficiaire"),
                        arrete_row.get("numero_dossier"),
                        arrete_row.get("date_signature"),
                        arrete_row.get("date_notification"),
                        arrete_row.get("document_id"),
                        Json(arrete_row["extraction"])
                        if arrete_row.get("extraction") is not None
                        else None,
                        arrete_row.get("extraction_modele"),
                        arrete_row.get("confiance"),
                    ),
                )
                aid = str(cur.fetchone()["id"])
                ids.append(aid)
                _inserer_prescriptions(cur, aid, prescriptions)
                n_presc += len(prescriptions)
            conn.commit()
            return {"arretes": len(ids), "prescriptions": n_presc, "ids": ids}
