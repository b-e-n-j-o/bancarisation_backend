"""01_Projet/projet.json — fiche, organisation, accès, mesures, unités de gestion."""

from __future__ import annotations

import psycopg

from api.export.contexte import ExportContexte, FichierExport
from api.export.modules._fichiers import fichier
from api.export.sqlutil import ecrire_json, lignes, une

nom = "projet"
dossier = "01_Projet"


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
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
    unites = []
    for table, genre in (
        ("unites_de_gestion_surf", "surf"),
        ("unites_de_gestion_lin", "lin"),
        ("unites_de_gestion_pct", "pct"),
    ):
        try:
            with conn.transaction():
                rows = lignes(
                    conn,
                    f"""
                    select id::text, ug_id, libelle
                      from bancarisation.{table}
                     where projet_id = %s
                     order by ug_id
                    """,
                    (pid,),
                )
        except Exception as exc:
            ctx.avertir(f"UG {genre} illisibles : {exc}")
            rows = []
        for row in rows:
            row["genre"] = genre
            unites.append(row)

    payload = {
        "projet": _sans_binaire(projet),
        "organisation": _sans_binaire(org or {}),
        "acces_partenaires": acces,
        "mesures": mesures,
        "unites_de_gestion": unites,
    }
    fic = fichier(ctx, f"{dossier}/projet.json", nom, "Fiche projet, mesures et UG", 1 + len(mesures))
    ecrire_json(fic.chemin_local, payload)
    return [fic]


def _sans_binaire(row: dict) -> dict:
    from api.export.sqlutil import pour_json

    return {
        cle: pour_json(valeur)
        for cle, valeur in row.items()
        if not isinstance(valeur, (bytes, memoryview))
    }
