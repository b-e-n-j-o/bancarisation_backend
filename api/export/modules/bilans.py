"""10_Bilans : PDF déjà archivés des bilans validés. Pas de régénération."""

from __future__ import annotations

import psycopg

from api.export.contexte import ExportContexte, FichierExport
from api.export.modules._fichiers import fichier
from api.export.modules.documents import DocumentAbsent, telecharger_binaire
from api.export.sqlutil import lignes

nom = "bilans"
dossier = "10_Bilans"


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    pid = str(ctx.projet_id)
    filtre_type = "" if ctx.options.inclure_budget else " and rb.type_bilan = 'ecologique'"
    rows = lignes(
        conn,
        f"""
        select rb.annee, rb.type_bilan, rb.version, d.bucket_path, d.nom_fichier
          from bancarisation.rapport_bilan rb
          left join bancarisation.documents d on d.id = rb.document_id
         where rb.projet_id = %s
           and rb.statut = 'valide'
           {filtre_type}
         order by rb.annee, rb.type_bilan, rb.version
        """,
        (pid,),
    )
    produits = []
    for row in rows:
        genre = "financiers" if row.get("type_bilan") == "financier" else "ecologiques"
        libelle = "bilan-financier" if genre == "financiers" else "bilan-ecologique"
        chemin = f"{dossier}/{genre}/{row['annee']}_{libelle}_VALIDE.pdf"
        if row.get("version") and int(row["version"]) > 1:
            chemin = f"{dossier}/{genre}/{row['annee']}_v{row['version']}_{libelle}_VALIDE.pdf"
        if not row.get("bucket_path"):
            ctx.avertir(
                f"Bilan {row.get('type_bilan')} {row.get('annee')} validé sans PDF archivé."
            )
            continue
        fic = fichier(ctx, chemin, nom, f"Bilan {libelle} {row['annee']}", compresser=False)
        try:
            telecharger_binaire(row["bucket_path"], fic.chemin_local)
        except DocumentAbsent as exc:
            ctx.avertir(f"PDF de bilan introuvable ({chemin}) : {exc}")
            continue
        produits.append(fic)
    return produits
