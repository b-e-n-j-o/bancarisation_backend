"""data/ : un CSV par table, filtré par projet, sous RLS."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql

from api.export.contexte import ExportContexte, FichierExport
from api.export.excel_util import ecrire_feuille, nouveau_classeur, sauver
from api.export.modules._fichiers import fichier
from api.export.sqlutil import lignes, table_existe

nom = "donnees"
dossier = "data"

# (fichier, table, mode de filtre)
# projet : projet_id
# org : organisation du projet
# lien : requête dédiée
TABLES = [
    ("projets", "projets", "id"),
    ("action_fiche", "action_fiche", "projet"),
    ("echeance", "echeance", "projet"),
    ("occurrence", "occurrence", "projet"),
    ("occurrence_finance", "occurrence_finance", "budget"),
    ("budget_baseline", "budget_baseline", "budget"),
    ("budget_mouvement", "budget_mouvement", "budget"),
    ("ligne_budget", "ligne_budget", "budget"),
    ("rapport_bilan", "rapport_bilan", "bilan"),
    ("unites_de_gestion_surf", "unites_de_gestion_surf", "projet"),
    ("unites_de_gestion_lin", "unites_de_gestion_lin", "projet"),
    ("unites_de_gestion_pct", "unites_de_gestion_pct", "projet"),
    ("emprise_projet", "emprise_projet", "projet"),
    ("documents", "documents", "projet"),
    ("personnes", "personnes", "personnes"),
    ("parcelles", "parcelles", "projet"),
    ("droits_fonciers", "droits_fonciers", "droits"),
    ("droit_documents", "droit_documents", "droit_documents"),
    ("ug_surf_parcelles", "ug_surf_parcelles", "projet"),
    ("ug_lin_parcelles", "ug_lin_parcelles", "projet"),
    ("ug_pct_parcelles", "ug_pct_parcelles", "projet"),
    ("demande", "demande", "projet"),
    ("demande_message", "demande_message", "messages"),
    ("arrete", "arrete", "projet"),
    ("arrete_prescription", "arrete_prescription", "prescriptions"),
    ("prescription_couverture", "prescription_couverture", "couverture"),
    ("projet_contact", "projet_contact", "projet"),
    ("note_interne", "note_interne", "projet"),
    ("action_etape", "action_etape", "projet"),
    ("plan_cao", "plan_cao", "projet"),
    ("plan_cao_calque", "plan_cao_calque", "projet"),
    ("plan_cao_groupe", "plan_cao_groupe", "plan"),
    ("plan_cao_entite", "plan_cao_entite", "projet"),
    ("checklist_modele", "checklist_modele", "org"),
    ("checklist_modele_etape", "checklist_modele_etape", "checklist"),
]

_COLONNES_BUDGET = {"montant_ht", "montant_engage", "montant_realise", "montant_initial", "annee_initiale", "montant_ttc", "taux_tva"}


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    pid = str(ctx.projet_id)
    org = str(ctx.organisation_id)
    produits: list[FichierExport] = []
    dictionnaire = []
    for fichier_nom, table, mode in TABLES:
        if mode == "budget" and not ctx.options.inclure_budget:
            continue
        if not table_existe(conn, table):
            ctx.avertir(f"data/{fichier_nom}.csv omis : table {table} absente.")
            continue
        try:
            with conn.transaction():
                n = _copier(conn, ctx, table, mode, pid, org, ctx.options.inclure_budget)
        except Exception as exc:
            ctx.avertir(f"data/{fichier_nom}.csv : {exc}")
            continue
        fic = fichier(ctx, f"{dossier}/{fichier_nom}.csv", nom, f"Table {table}", n)
        produits.append(fic)
        dictionnaire.extend(_commentaires(conn, table, f"{fichier_nom}.csv"))
    wb = nouveau_classeur()
    ws = wb.active
    ws.title = "Dictionnaire"
    ecrire_feuille(
        ws,
        ["Fichier", "Colonne", "Type", "Description"],
        dictionnaire,
    )
    dico = fichier(ctx, f"{dossier}/dictionnaire_donnees.xlsx", nom, "Dictionnaire des CSV", len(dictionnaire))
    sauver(wb, dico.chemin_local)
    produits.append(dico)
    return produits


def _copier(conn, ctx, table: str, mode: str, pid: str, org: str, budget: bool) -> int:
    colonnes = lignes(
        conn,
        """
        select column_name, udt_name
          from information_schema.columns
         where table_schema = 'bancarisation' and table_name = %s
         order by ordinal_position
        """,
        (table,),
    )
    select = []
    for col in colonnes:
        nom_col = col["column_name"]
        if table == "occurrence" and not budget and nom_col in _COLONNES_BUDGET:
            continue
        if table == "rapport_bilan" and not budget and nom_col == "donnees":
            select.append(
                "case when type_bilan = 'financier' then null else donnees end as donnees"
            )
            continue
        if col["udt_name"] == "geometry":
            select.append(
                f'CASE WHEN "{nom_col}" IS NULL THEN NULL '
                f'WHEN ST_SRID("{nom_col}") IN (0, 2154) THEN ST_AsText("{nom_col}") '
                f'ELSE ST_AsText(ST_Transform("{nom_col}", 2154)) END AS "{nom_col}_wkt"'
            )
        else:
            select.append(f'"{nom_col}"')
    if not select:
        select.append("null")
    requete = sql.SQL("select {cols} from bancarisation.{table} t where {filtre}").format(
        cols=sql.SQL(", ").join(sql.SQL(morceau) for morceau in select),
        table=sql.Identifier(table),
        filtre=_filtre(mode, budget),
    )
    params = _params(mode, pid, org)
    compte = conn.execute(
        sql.SQL("select count(*) as n from bancarisation.{table} t where {filtre}").format(
            table=sql.Identifier(table),
            filtre=_filtre(mode, budget),
        ),
        params,
    ).fetchone()["n"]
    cible: Path = ctx.dossier_tmp / dossier / f"{table}.csv"
    cible.parent.mkdir(parents=True, exist_ok=True)
    copie = sql.SQL("copy ({}) to stdout with (format csv, header, encoding 'UTF8')").format(requete)
    with cible.open("wb") as sortie:
        with conn.cursor().copy(copie, params) as flux:
            for bloc in flux:
                sortie.write(bloc)
    return int(compte)


def _filtre(mode: str, budget: bool) -> sql.SQL:
    if mode == "id":
        return sql.SQL("t.id = %s")
    if mode == "projet":
        return sql.SQL("t.projet_id = %s")
    if mode == "budget":
        return sql.SQL("t.projet_id = %s")
    if mode == "bilan":
        if budget:
            return sql.SQL("t.projet_id = %s")
        return sql.SQL("t.projet_id = %s and coalesce(t.type_bilan, 'ecologique') <> 'financier'")
    if mode == "org":
        return sql.SQL("t.organisation_id = %s")
    if mode == "personnes":
        return sql.SQL(
            """t.id in (
                select personne_id from bancarisation.projet_contact where projet_id = %s
                union
                select titulaire_id from bancarisation.droits_fonciers df
                  join bancarisation.parcelles pa on pa.id = df.parcelle_id
                 where pa.projet_id = %s and df.titulaire_id is not null
                union
                select constituant_id from bancarisation.droits_fonciers df
                  join bancarisation.parcelles pa on pa.id = df.parcelle_id
                 where pa.projet_id = %s and df.constituant_id is not null
            )"""
        )
    if mode == "droits":
        return sql.SQL(
            "t.parcelle_id in (select id from bancarisation.parcelles where projet_id = %s)"
        )
    if mode == "droit_documents":
        return sql.SQL(
            """t.droit_id in (
                select df.id from bancarisation.droits_fonciers df
                  join bancarisation.parcelles pa on pa.id = df.parcelle_id
                 where pa.projet_id = %s
            )"""
        )
    if mode == "messages":
        return sql.SQL(
            "t.demande_id in (select id from bancarisation.demande where projet_id = %s)"
        )
    if mode == "prescriptions":
        return sql.SQL(
            "t.arrete_id in (select id from bancarisation.arrete where projet_id = %s)"
        )
    if mode == "couverture":
        return sql.SQL(
            """t.prescription_id in (
                select pr.id from bancarisation.arrete_prescription pr
                  join bancarisation.arrete a on a.id = pr.arrete_id
                 where a.projet_id = %s
            )"""
        )
    if mode == "plan":
        return sql.SQL(
            "t.plan_id in (select id from bancarisation.plan_cao where projet_id = %s)"
        )
    if mode == "checklist":
        return sql.SQL(
            """t.modele_id in (
                select id from bancarisation.checklist_modele where organisation_id = %s
            )"""
        )
    raise RuntimeError(f"Filtre inconnu : {mode}")


def _params(mode: str, pid: str, org: str) -> tuple:
    if mode == "org" or mode == "checklist":
        return (org,)
    if mode == "personnes":
        return (pid, pid, pid)
    return (pid,)


def _commentaires(conn, table: str, fichier_nom: str) -> list[list]:
    rows = lignes(
        conn,
        """
        select a.attname as colonne,
               format_type(a.atttypid, a.atttypmod) as type,
               col_description(c.oid, a.attnum) as description,
               a.attgenerated as generee
          from pg_attribute a
          join pg_class c on c.oid = a.attrelid
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'bancarisation'
           and c.relname = %s
           and a.attnum > 0
           and not a.attisdropped
         order by a.attnum
        """,
        (table,),
    )
    generees = {
        "geom_3857", "geom_4326", "geom_local", "surface_calculee_m2",
        "bbox_local", "geom_intersection",
    }
    return [
        [
            fichier_nom,
            r["colonne"],
            r["type"],
            (
                "Générée ou dérivée — ne pas réinsérer. " + (r.get("description") or "")
                if r["colonne"] in generees or r.get("generee") in ("s", "v")
                else (r.get("description") or "")
            ),
        ]
        for r in rows
    ]
