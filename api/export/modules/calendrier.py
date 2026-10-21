"""02_Calendrier : actions.xlsx, calendrier.ics, regles_recurrence.xlsx."""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import date

import psycopg

from api.calendrier_ics import generer_ics
from api.export.contexte import ExportContexte, FichierExport
from api.export.excel_util import ecrire_feuille, libelle_statut, nouveau_classeur, sauver
from api.export.modules._fichiers import fichier
from api.export.sqlutil import annee_clause, lignes

nom = "calendrier"
dossier = "02_Calendrier"

_TYPES_RECURRENCE = {
    "ponctuel": "Ponctuel",
    "periodique": "Périodique",
    "campagnes": "Campagnes",
    "dependant_evenement": "Dépendant d'un événement",
}


def charger(ctx: ExportContexte, conn: psycopg.Connection) -> dict:
    """Actions, checklists et règles. Excel, ICS et planning PDF lisent ce résultat."""
    pid = str(ctx.projet_id)
    filtre, params = annee_clause("o", ctx.options.annee_min, ctx.options.annee_max)
    supprime = "" if ctx.options.inclure_supprimees else " and o.statut <> 'supprime'"
    actions = lignes(
        conn,
        f"""
        select o.id::text, o.annee, o.mois_debut, o.mois_fin, o.traverse_nouvel_an,
               o.statut, o.date_realisation, o.origine, o.titre, o.code,
               coalesce(o.ug_ids, '{{}}'::text[]) as ug_ids,
               coalesce(af.code, '') as mesure_code,
               coalesce(af.titre, '') as mesure_titre,
               coalesce(e.libelle, o.titre) as libelle_action
          from bancarisation.occurrence o
          left join bancarisation.echeance e on e.id = o.echeance_id
          left join bancarisation.action_fiche af
            on af.projet_id = o.projet_id and af.cle = e.action_cle
         where o.projet_id = %s
           {supprime}
           {filtre}
         order by o.annee, o.mois_debut, o.code
        """,
        [pid, *params],
    )
    return {
        "pid": pid,
        "ugs": _index_ug(conn, pid),
        "actions": actions,
        "etapes": _etapes(conn, pid),
        "regles": _regles(conn, pid),
        "nom_projet": (lignes(conn, "select nom from bancarisation.projets where id = %s", (pid,)) or [{"nom": "Projet"}])[0]["nom"],
    }


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    charge = charger(ctx, conn)
    pid = charge["pid"]
    ugs = charge["ugs"]
    actions = charge["actions"]
    etapes = charge["etapes"]
    compte_etapes = {}
    for etape in etapes:
        cle = etape["occurrence_id"]
        fait, total = compte_etapes.get(cle, (0, 0))
        compte_etapes[cle] = (fait + (1 if etape.get("fait") else 0), total + 1)

    url = ctx.url_front.rstrip("/")
    lignes_actions = []
    pour_ics = []
    for action in actions:
        ug = _libelles_ug(action.get("ug_ids") or [], ugs)
        fait, total = compte_etapes.get(action["id"], (0, 0))
        checklist = f"{fait}/{total}" if total else ""
        debut, fin = _dates_fenetre(action)
        lien = f"{url}/projets/{pid}?vue=calendar&occurrence={action['id']}"
        mesure = " ".join(
            p for p in (action.get("mesure_code"), action.get("mesure_titre")) if p
        ).strip()
        lignes_actions.append([
            action["id"],
            mesure,
            action.get("libelle_action") or action.get("titre"),
            ug,
            action.get("annee"),
            action.get("mois_debut"),
            action.get("mois_fin"),
            debut,
            fin,
            libelle_statut(action.get("statut")),
            action.get("date_realisation"),
            action.get("origine") or "",
            checklist,
            lien,
        ])
        pour_ics.append({
            "id": action["id"],
            "annee": action.get("annee"),
            "mois_debut": action.get("mois_debut"),
            "mois_fin": action.get("mois_fin"),
            "traverse_nouvel_an": action.get("traverse_nouvel_an"),
            "statut": action.get("statut"),
            "titre": action.get("libelle_action") or action.get("titre"),
            "mesure": mesure,
            "ug": ug,
            "fin": fin,
        })

    wb = nouveau_classeur()
    ws = wb.active
    ws.title = "Actions"
    ecrire_feuille(
        ws,
        [
            "Id", "Mesure", "Action", "UG", "Année", "Mois début", "Mois fin",
            "Début de fenêtre", "Fin de fenêtre", "Statut", "Date de réalisation",
            "Origine", "Checklist", "Lien KerERC",
        ],
        lignes_actions,
        colonnes_date=(8, 9, 11),
        colonne_statut=10,
    )
    if etapes:
        ws2 = wb.create_sheet("Checklists")
        ecrire_feuille(
            ws2,
            ["Occurrence", "Ordre", "Libellé", "Fait", "Échéance", "Assigné"],
            [
                [
                    e.get("occurrence_id"),
                    e.get("ordre"),
                    e.get("libelle"),
                    "Oui" if e.get("fait") else "Non",
                    e.get("echeance"),
                    e.get("assigne_nom") or "",
                ]
                for e in etapes
            ],
            colonnes_date=(5,),
        )
    elif not _table_existe(conn, "action_etape"):
        ctx.avertir("Checklists : table action_etape absente, feuille omise.")

    fic_xlsx = fichier(ctx, f"{dossier}/actions.xlsx", nom, "Actions du calendrier", len(lignes_actions))
    sauver(wb, fic_xlsx.chemin_local)

    nom_projet = charge["nom_projet"]
    ics = generer_ics(
        pour_ics,
        nom_projet=nom_projet,
        url_front=ctx.url_front,
        projet_id=pid,
        mode_titre="complet",
    )
    fic_ics = fichier(ctx, f"{dossier}/calendrier.ics", nom, "Calendrier des fenêtres", len(pour_ics), compresser=True)
    fic_ics.chemin_local.write_bytes(ics)

    regles = charge["regles"]
    wb_regles = nouveau_classeur()
    feuille = wb_regles.active
    feuille.title = "Règles"
    ecrire_feuille(
        feuille,
        [
            "Clé", "Mesure", "Libellé", "Récurrence", "Fenêtre",
            "Page source", "Confiance", "Champs à confirmer", "Avertissements", "Origine",
        ],
        regles,
    )
    fic_regles = fichier(ctx, f"{dossier}/regles_recurrence.xlsx", nom, "Règles de récurrence", len(regles))
    sauver(wb_regles, fic_regles.chemin_local)
    produits = [fic_xlsx, fic_ics, fic_regles]
    try:
        from datetime import datetime

        from api.export.pdf.planning import ecrire_planning

        planning = fichier(ctx, f"{dossier}/planning_pluriannuel.pdf", nom, "Planning pluriannuel", len(pour_ics), compresser=False)
        ecrire_planning(planning.chemin_local, nom_projet, datetime.now().strftime("%d/%m/%Y"), pour_ics)
        produits.append(planning)
    except Exception as exc:
        ctx.avertir(f"Planning PDF : {exc}")
    return produits


def _etapes(conn, pid: str) -> list[dict]:
    if not _table_existe(conn, "action_etape"):
        return []
    try:
        with conn.transaction():
            return lignes(
                conn,
                """
                select ae.occurrence_id::text, ae.ordre, ae.libelle, ae.fait, ae.echeance,
                       coalesce(nullif(trim(concat_ws(' ', p.prenom, p.nom)), ''), '') as assigne_nom
                  from bancarisation.action_etape ae
                  left join bancarisation.personnes p on p.id = ae.assigne_a
                 where ae.projet_id = %s
                 order by ae.occurrence_id, ae.ordre
                """,
                (pid,),
            )
    except Exception:
        return lignes(
            conn,
            """
            select occurrence_id::text, ordre, libelle, fait, null::date as echeance, '' as assigne_nom
              from bancarisation.action_etape
             where projet_id = %s
             order by occurrence_id, ordre
            """,
            (pid,),
        )


def _regles(conn, pid: str) -> list[list]:
    rows = lignes(
        conn,
        """
        select e.cle, coalesce(af.code, e.action_cle) as mesure, e.libelle, e.recurrence,
               e.fenetre_debut, e.fenetre_fin, e.source_page, e.confiance,
               e.champs_a_confirmer, e.avertissements, e.code_operation
          from bancarisation.echeance e
          left join bancarisation.action_fiche af
            on af.projet_id = e.projet_id and af.cle = e.action_cle
         where e.projet_id = %s
         order by e.cle
        """,
        (pid,),
    )
    sortie = []
    for row in rows:
        fenetre = ""
        if row.get("fenetre_debut") or row.get("fenetre_fin"):
            fenetre = f"{row.get('fenetre_debut') or '—'} → {row.get('fenetre_fin') or '—'}"
        sortie.append([
            row.get("cle"),
            row.get("mesure"),
            row.get("libelle"),
            _recurrence(row.get("recurrence")),
            fenetre,
            row.get("source_page"),
            row.get("confiance"),
            _liste(row.get("champs_a_confirmer")),
            _liste(row.get("avertissements")),
            "",
        ])
    return sortie


def _recurrence(valeur) -> str:
    if not isinstance(valeur, dict):
        if valeur is None:
            return ""
        if isinstance(valeur, str):
            try:
                valeur = json.loads(valeur)
            except json.JSONDecodeError:
                return valeur
        else:
            return str(valeur)
    type_ = str(valeur.get("type") or "")
    libelle = _TYPES_RECURRENCE.get(type_, type_ or "—")
    details = [
        f"{cle} : {val}"
        for cle, val in valeur.items()
        if cle != "type" and val not in (None, "", [], {})
    ]
    return libelle if not details else f"{libelle} — " + ", ".join(details)


def _liste(valeur) -> str:
    if isinstance(valeur, list):
        return ", ".join(str(v) for v in valeur)
    return "" if valeur is None else str(valeur)


def _index_ug(conn, pid: str) -> dict[str, str]:
    index: dict[str, str] = {}
    for table in ("unites_de_gestion_surf", "unites_de_gestion_lin", "unites_de_gestion_pct"):
        try:
            with conn.transaction():
                rows = lignes(
                    conn,
                    f"select ug_id, libelle from bancarisation.{table} where projet_id = %s",
                    (pid,),
                )
        except Exception:
            continue
        for row in rows:
            if row.get("ug_id"):
                index[str(row["ug_id"])] = row.get("libelle") or str(row["ug_id"])
    return index


def _libelles_ug(ids, index: dict[str, str]) -> str:
    return ", ".join(index.get(str(i), str(i)) for i in ids)


def _dates_fenetre(action: dict) -> tuple[date | None, date | None]:
    annee = action.get("annee")
    mois_debut = action.get("mois_debut")
    mois_fin = action.get("mois_fin")
    if not annee or not mois_debut or not mois_fin:
        return None, None
    annee_fin = int(annee) + (1 if action.get("traverse_nouvel_an") else 0)
    debut = date(int(annee), int(mois_debut), 1)
    try:
        fin = date(annee_fin, int(mois_fin), monthrange(annee_fin, int(mois_fin))[1])
    except ValueError:
        return debut, None
    return debut, fin


def _table_existe(conn, table: str) -> bool:
    from api.export.sqlutil import table_existe

    return table_existe(conn, table)
