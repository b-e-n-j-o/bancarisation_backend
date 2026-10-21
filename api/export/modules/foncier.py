"""07_Foncier : parcelles, droits (libellés UI) et personnes."""

from __future__ import annotations

import psycopg

from api.export.contexte import ExportContexte, FichierExport
from api.export.excel_util import ecrire_feuille, libelle_statut, nouveau_classeur, sauver
from api.export.modules._fichiers import fichier
from api.export.sqlutil import lignes

nom = "foncier"
dossier = "07_Foncier"

_NATURES = {
    "propriete": "Propriété",
    "usufruit": "Usufruit",
    "nue_propriete": "Nue-propriété",
    "bail_rural": "Bail rural",
    "bail_rural_clauses_env": "Bail rural à clauses environnementales",
    "bail_emphyteotique": "Bail emphytéotique",
    "ore": "ORE",
    "convention_gestion": "Convention de gestion",
    "cmd_safer": "Convention de mise à disposition SAFER",
    "convention_paturage": "Convention de pâturage",
    "commodat": "Commodat",
    "aot": "Autorisation d'occupation temporaire",
    "servitude": "Servitude",
    "droit_chasse": "Droit de chasse",
    "regime_forestier": "Régime forestier",
    "occupation_sans_titre": "Occupation sans titre",
    "autre": "Autre",
}


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    pid = str(ctx.projet_id)
    parcelles = lignes(
        conn,
        """
        select pa.id::text, pa.idu, pa.commune, pa.section, pa.numero,
               pa.surface_calculee_m2, pa.contenance_m2,
               string_agg(distinct ug.libelle, ', ') as ug
          from bancarisation.parcelles pa
          left join bancarisation.v_ug_parcelles ug on ug.parcelle_id = pa.id
         where pa.projet_id = %s
         group by pa.id
         order by pa.commune, pa.section, pa.numero
        """,
        (pid,),
    )
    droits = lignes(
        conn,
        """
        select df.nature, df.statut, df.date_debut, df.date_fin, df.observations,
               df.assiette, df.ug_code,
               pa.idu, pa.commune, pa.section, pa.numero,
               tit.nom as titulaire_nom, tit.prenom as titulaire_prenom,
               con.nom as constituant_nom, con.prenom as constituant_prenom
          from bancarisation.droits_fonciers df
          join bancarisation.parcelles pa on pa.id = df.parcelle_id
          left join bancarisation.personnes tit on tit.id = df.titulaire_id
          left join bancarisation.personnes con on con.id = df.constituant_id
         where pa.projet_id = %s
         order by pa.commune, pa.section, pa.numero
        """,
        (pid,),
    )
    personnes = lignes(
        conn,
        """
        select distinct p.nom, p.prenom, p.type_personne, p.email, p.telephone, p.fonction
          from bancarisation.personnes p
         where p.id in (
            select titulaire_id from bancarisation.droits_fonciers df
              join bancarisation.parcelles pa on pa.id = df.parcelle_id
             where pa.projet_id = %s and df.titulaire_id is not null
            union
            select constituant_id from bancarisation.droits_fonciers df
              join bancarisation.parcelles pa on pa.id = df.parcelle_id
             where pa.projet_id = %s and df.constituant_id is not null
            union
            select personne_id from bancarisation.projet_contact where projet_id = %s
         )
         order by p.nom
        """,
        (pid, pid, pid),
    )
    wb = nouveau_classeur()
    ws = wb.active
    ws.title = "Parcelles"
    ecrire_feuille(
        ws,
        ["Référence cadastrale", "Commune", "Surface calculée (m²)", "Contenance (m²)", "UG liées"],
        [
            [_ref(p), p.get("commune"), _n(p.get("surface_calculee_m2")), _n(p.get("contenance_m2")), p.get("ug") or ""]
            for p in parcelles
        ],
    )
    ws_d = wb.create_sheet("Droits")
    ecrire_feuille(
        ws_d,
        ["Parcelle", "Nature", "Titulaire", "Constituant", "Début", "Fin", "Statut", "Assiette", "UG", "Observations"],
        [
            [
                _ref(d),
                _NATURES.get(d.get("nature"), d.get("nature")),
                _personne(d.get("titulaire_prenom"), d.get("titulaire_nom")),
                _personne(d.get("constituant_prenom"), d.get("constituant_nom")),
                d.get("date_debut"),
                d.get("date_fin"),
                libelle_statut(d.get("statut")),
                d.get("assiette") or "",
                d.get("ug_code") or "",
                d.get("observations") or "",
            ]
            for d in droits
        ],
        colonnes_date=(5, 6),
        colonne_statut=7,
    )
    ws_p = wb.create_sheet("Personnes")
    ecrire_feuille(
        ws_p,
        ["Nom", "Prénom", "Type", "Fonction", "Email", "Téléphone"],
        [
            [p.get("nom"), p.get("prenom"), p.get("type_personne"), p.get("fonction"), p.get("email"), p.get("telephone")]
            for p in personnes
        ],
    )
    fic = fichier(ctx, f"{dossier}/parcelles_droits.xlsx", nom, "Parcelles et droits", len(parcelles))
    sauver(wb, fic.chemin_local)
    return [fic]


def _ref(row: dict) -> str:
    if row.get("idu"):
        return str(row["idu"])
    return " ".join(str(row.get(c) or "") for c in ("commune", "section", "numero")).strip()


def _personne(prenom, nom_famille) -> str:
    return " ".join(p for p in (prenom, nom_famille) if p).strip()


def _n(valeur):
    return None if valeur is None else float(valeur)
