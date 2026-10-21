"""01_Projet/projet.json — fiche, organisation, accès, mesures, unités de gestion."""

from __future__ import annotations

import psycopg

from api.export.contexte import ExportContexte, FichierExport
from api.export.modules._fichiers import fichier
from api.export.sqlutil import ecrire_json, lignes, une

nom = "projet"
dossier = "01_Projet"


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    charge = charger(ctx, conn)
    fic = fichier(ctx, f"{dossier}/projet.json", nom, "Fiche projet, mesures et UG", 1 + len(charge["mesures"]))
    ecrire_json(fic.chemin_local, {
        "projet": charge["projet"],
        "organisation": charge["organisation"],
        "acces_partenaires": charge["acces_partenaires"],
        "mesures": charge["mesures"],
        "unites_de_gestion": charge["unites_de_gestion"],
    })
    produits = [fic]
    try:
        from datetime import datetime

        from api.export.pdf.fiche_projet import contexte_fiche, ecrire_fiche

        pdf = fichier(ctx, f"{dossier}/fiche_projet.pdf", nom, "Fiche projet", 1, compresser=False)
        ecrire_fiche(pdf.chemin_local, contexte_fiche(
            charge["projet"].get("nom") or "Projet",
            datetime.now().strftime("%d/%m/%Y"),
            charge["organisation"],
            charge["acces_partenaires"],
            charge["mesures"],
            charge["unites_de_gestion"],
            charge["identite"],
            charge["chiffres"],
        ))
        produits.append(pdf)
    except Exception as exc:
        ctx.avertir(f"Fiche projet PDF : {exc}")
    return produits


def charger(ctx: ExportContexte, conn: psycopg.Connection) -> dict:
    pid = str(ctx.projet_id)
    projet = une(conn, "select * from bancarisation.projets where id = %s", (pid,))
    if not projet:
        raise RuntimeError("Projet introuvable sous la RLS du demandeur.")
    org = une(
        conn,
        "select * from bancarisation.organisations where id = %s",
        (str(projet["organisation_id"]),),
    )
    acces = lignes(
        conn,
        """
        select pa.organisation_id::text, o.nom as organisation, pa.qualite, pa.niveau,
               pa.voir_finances, pa.expire_le
          from bancarisation.projet_acces pa
          left join bancarisation.organisations o on o.id = pa.organisation_id
         where pa.projet_id = %s
           and pa.organisation_id is not null
         order by o.nom
        """,
        (pid,),
    )
    mesures = lignes(
        conn,
        """
        select id::text, cle, code, categorie, titre, lib_thema, ug_ids, confiance
          from bancarisation.action_fiche
         where projet_id = %s
         order by code
        """,
        (pid,),
    )
    unites = _unites(ctx, conn, pid, mesures)
    return {
        "projet": _sans_binaire(projet),
        "organisation": _sans_binaire(org or {}),
        "acces_partenaires": acces,
        "mesures": mesures,
        "unites_de_gestion": unites,
        "identite": _identite(projet),
        "chiffres": _chiffres(ctx, conn, pid),
    }


def _unites(ctx, conn, pid: str, mesures: list[dict]) -> list[dict]:
    unites = []
    for table, genre, metrique in (
        ("unites_de_gestion_surf", "surf", "round(ST_Area(geom)::numeric, 0)"),
        ("unites_de_gestion_lin", "lin", "round(ST_Length(geom)::numeric, 0)"),
        ("unites_de_gestion_pct", "pct", "null"),
    ):
        try:
            with conn.transaction():
                rows = lignes(
                    conn,
                    f"""
                    select id::text, ug_id, libelle, {metrique} as metrique
                      from bancarisation.{table}
                     where projet_id = %s
                     order by ug_id
                    """,
                    (pid,),
                )
        except Exception as exc:
            ctx.avertir(f"UG {genre} illisibles : {exc}")
            rows = []
        unite = "m²" if genre == "surf" else ("m" if genre == "lin" else "")
        for row in rows:
            code = str(row.get("ug_id") or "")
            liees = [
                " ".join(p for p in (m.get("code"), m.get("titre")) if p).strip()
                for m in mesures
                if code and code in (m.get("ug_ids") or [])
            ]
            row["genre"] = genre
            row["mesures"] = " | ".join(liees)
            if row.get("metrique") is not None and unite:
                row["metrique_libelle"] = f"{row['metrique']} {unite}"
            unites.append(row)
    return unites


def _identite(projet: dict) -> list[tuple[str, object]]:
    champs = [
        ("Nom", "nom"),
        ("Référence interne", "reference_interne"),
        ("Commune", "commune"),
        ("Département", "departement"),
        ("Procédure", "type_procedure"),
        ("Référence de la décision", "reference_decision"),
        ("Référence EI", "reference_ei"),
        ("Date de décision", "date_decision"),
        ("Durée (années)", "duree_annees"),
        ("Date de fin", "date_fin"),
        ("Statut", "statut"),
    ]
    return [(libelle, projet.get(cle)) for libelle, cle in champs]


def _chiffres(ctx: ExportContexte, conn, pid: str) -> list[tuple[str, object]]:
    from api.export.excel_util import libelle_statut

    comptes = lignes(
        conn,
        """
        select statut, count(*)::int as n
          from bancarisation.occurrence
         where projet_id = %s
         group by statut
         order by statut
        """,
        (pid,),
    )
    prochaine = une(
        conn,
        """
        select titre, annee, mois_debut
          from bancarisation.occurrence
         where projet_id = %s
           and statut not in ('realise', 'supprime', 'archive')
         order by annee, mois_debut nulls last
         limit 1
        """,
        (pid,),
    )
    documents = une(
        conn,
        "select count(*)::int as n from bancarisation.documents where projet_id = %s",
        (pid,),
    )
    lignes_chiffres = [
        (f"Actions {libelle_statut(row['statut'])}", row["n"]) for row in comptes
    ]
    if prochaine:
        lignes_chiffres.append((
            "Prochaine échéance",
            f"{prochaine.get('titre') or '—'} ({prochaine.get('annee') or '—'}"
            f"/{prochaine.get('mois_debut') or '—'})",
        ))
    lignes_chiffres.append(("Documents", (documents or {}).get("n") or 0))
    if ctx.options.inclure_budget:
        from api.budget.lecture_export import charger_budget_projet

        budget = charger_budget_projet(conn, pid, ctx.options.annee_min, ctx.options.annee_max)
        prevu = sum(float(l.get("montant_ht") or 0) for l in budget["detail"])
        realise = sum(float(l.get("montant_realise") or 0) for l in budget["detail"])
        lignes_chiffres.append(("Budget prévu", f"{prevu:,.2f} €".replace(",", " ")))
        lignes_chiffres.append(("Budget réalisé", f"{realise:,.2f} €".replace(",", " ")))
    return lignes_chiffres


def _sans_binaire(row: dict) -> dict:
    from api.export.sqlutil import pour_json

    return {
        cle: pour_json(valeur)
        for cle, valeur in row.items()
        if not isinstance(valeur, (bytes, memoryview))
    }
