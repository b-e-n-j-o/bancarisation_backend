"""09_Echanges : fil, demandes et notes. Les PDF sont au lot 2.

La RLS de note_interne limite la lecture à l'organisation autrice.
"""

from __future__ import annotations

import psycopg

from api.export.contexte import ExportContexte, FichierExport
from api.export.modules._fichiers import fichier
from api.export.sqlutil import ecrire_json, lignes, pour_json, une

nom = "echanges"
dossier = "09_Echanges"


def charger(ctx: ExportContexte, conn: psycopg.Connection) -> dict:
    pid = str(ctx.projet_id)
    fil = []
    try:
        with conn.transaction():
            fil = lignes(
                conn,
                """
                select survenu_le, type, acteur, libelle, demande_id::text
                  from bancarisation.v_projet_activite
                 where projet_id = %s
                 order by survenu_le
                """,
                (pid,),
            )
    except Exception as exc:
        ctx.avertir(f"Fil d'activité indisponible : {exc}")

    demandes = lignes(
        conn,
        """
        select id::text, occurrence_id::text, annee, signal_code, objet, statut,
               auteur, cree_le, maj_le, clos_le
          from bancarisation.demande
         where projet_id = %s
         order by cree_le
        """,
        (pid,),
    )
    messages = lignes(
        conn,
        """
        select m.id::text, m.demande_id::text, m.acteur, m.auteur_nom, m.corps,
               m.documents_ids, m.origine, m.cree_le
          from bancarisation.demande_message m
          join bancarisation.demande d on d.id = m.demande_id
         where d.projet_id = %s
         order by m.cree_le
        """,
        (pid,),
    )
    par_demande: dict[str, list] = {}
    for message in messages:
        par_demande.setdefault(message["demande_id"], []).append(message)

    notes = lignes(
        conn,
        """
        select n.occurrence_id::text as action_id, n.contenu, n.created_at,
               n.organisation_id::text,
               o.nom as organisation,
               coalesce(nullif(trim(concat_ws(' ', pr.prenom, pr.nom)), ''), '') as auteur,
               coalesce(af.titre, af.code, '') as mesure,
               occ.titre as action,
               occ.annee
          from bancarisation.note_interne n
          left join bancarisation.organisations o on o.id = n.organisation_id
          left join bancarisation.personnes pr on pr.id = n.auteur_id
          left join bancarisation.occurrence occ on occ.id = n.occurrence_id
          left join bancarisation.echeance e on e.id = occ.echeance_id
          left join bancarisation.action_fiche af
            on af.projet_id = n.projet_id and af.cle = e.action_cle
         where n.projet_id = %s
           and n.supprime_le is null
         order by n.created_at
        """,
        (pid,),
    )

    projet = une(conn, "select nom from bancarisation.projets where id = %s", (pid,))
    payload = {
        "fil_projet": pour_json(fil),
        "demandes": [
            {
                "demande": pour_json(d),
                "messages": [
                    {
                        **pour_json(m),
                        "pieces": [str(i) for i in (m.get("documents_ids") or [])],
                    }
                    for m in par_demande.get(d["id"], [])
                ],
            }
            for d in demandes
        ],
        "notes": pour_json(notes),
        "projet_nom": (projet or {}).get("nom") or "Projet",
    }
    return payload


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    from datetime import datetime

    payload = charger(ctx, conn)
    fic = fichier(
        ctx,
        f"{dossier}/echanges.json",
        nom,
        "Échanges et notes internes",
        len(payload["demandes"]) + len(payload["notes"]),
    )
    ecrire_json(fic.chemin_local, payload)
    produits = [fic]
    if not payload["demandes"] and not payload["notes"] and not payload["fil_projet"]:
        ctx.avertir("Échanges : ni demande ni note, PDF non générés.")
        return produits
    genere = datetime.now().strftime("%d/%m/%Y")
    projet = payload.get("projet_nom") or "Projet"
    try:
        from api.export.pdf.echanges import ecrire_fil, ecrire_notes

        if payload["demandes"] or payload["fil_projet"]:
            fil = fichier(ctx, f"{dossier}/fil_du_projet.pdf", nom, "Fil du projet", len(payload["fil_projet"]), compresser=False)
            ecrire_fil(fil.chemin_local, projet, genere, payload, ctx.alias_fichiers)
            produits.append(fil)
        if payload["notes"]:
            notes = fichier(ctx, f"{dossier}/notes_par_action.pdf", nom, "Notes par action", len(payload["notes"]), compresser=False)
            ecrire_notes(notes.chemin_local, projet, genere, payload)
            produits.append(notes)
    except Exception as exc:
        ctx.avertir(f"PDF des échanges : {exc}")
    return produits
