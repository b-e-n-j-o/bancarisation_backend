"""08_Obligations : arrêtés et prescriptions couvertes (liens mode=user seulement)."""

from __future__ import annotations

import psycopg

from api.export.contexte import ExportContexte, FichierExport
from api.export.excel_util import ecrire_feuille, nouveau_classeur, sauver
from api.export.modules._fichiers import fichier
from api.export.nommage import slug
from api.export.sqlutil import lignes

nom = "obligations"
dossier = "08_Obligations"


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    pid = str(ctx.projet_id)
    arretes = lignes(
        conn,
        """
        select a.id::text, a.reference, a.date_signature, a.autorite, a.type,
               d.nom as document_nom, d.categorie, d.nom_fichier, d.date_document, d.created_at
          from bancarisation.arrete a
          left join bancarisation.documents d on d.id = a.document_id
         where a.projet_id = %s
         order by a.date_signature nulls last
        """,
        (pid,),
    )
    prescriptions = lignes(
        conn,
        """
        select pr.id::text, a.reference as arrete, pr.article, pr.intitule, pr.categorie,
               pr.texte_source,
               string_agg(e.libelle, ' | ') filter (where c.mode = 'user') as couverte_par
          from bancarisation.arrete_prescription pr
          join bancarisation.arrete a on a.id = pr.arrete_id
          left join bancarisation.prescription_couverture c on c.prescription_id = pr.id
          left join bancarisation.echeance e on e.id = c.echeance_id and c.mode = 'user'
         where a.projet_id = %s
         group by pr.id, a.reference, pr.article, pr.intitule, pr.categorie, pr.texte_source
         order by a.reference, pr.article
        """,
        (pid,),
    )
    wb = nouveau_classeur()
    ws = wb.active
    ws.title = "Arrêtés"
    ecrire_feuille(
        ws,
        ["Référence", "Date", "Autorité", "Type", "PDF dans l'export"],
        [
            [
                a.get("reference"),
                a.get("date_signature"),
                a.get("autorite"),
                a.get("type"),
                _lien_pdf(a),
            ]
            for a in arretes
        ],
        colonnes_date=(2,),
    )
    ws_p = wb.create_sheet("Prescriptions")
    ecrire_feuille(
        ws_p,
        ["Arrêté", "Article", "Intitulé", "Catégorie", "Couverte par", "Extrait"],
        [
            [
                p.get("arrete"), p.get("article"), p.get("intitule"), p.get("categorie"),
                p.get("couverte_par") or "",
                (p.get("texte_source") or "")[:500],
            ]
            for p in prescriptions
        ],
    )
    fic = fichier(ctx, f"{dossier}/prescriptions.xlsx", nom, "Arrêtés et prescriptions", len(prescriptions))
    sauver(wb, fic.chemin_local)
    return [fic]


def _lien_pdf(arrete: dict) -> str:
    if not arrete.get("document_nom") and not arrete.get("nom_fichier"):
        return ""
    from datetime import datetime

    moment = arrete.get("date_document") or arrete.get("created_at")
    if isinstance(moment, datetime):
        annee = str(moment.year)
        jour = moment.date().isoformat()
    elif hasattr(moment, "year"):
        annee = str(moment.year)
        jour = moment.isoformat()[:10]
    else:
        annee, jour = "sans-date", "0000-00-00"
    nom_fichier = arrete.get("nom_fichier") or arrete.get("document_nom") or "arrete"
    from pathlib import Path

    return (
        f"../05_Documents/Arretes/{annee}/"
        f"{jour}_{slug(Path(nom_fichier).stem, 50)}{Path(nom_fichier).suffix.lower()}"
    )
