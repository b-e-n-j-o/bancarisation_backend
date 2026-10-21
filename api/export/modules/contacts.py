"""06_Contacts : tableur et vCard 3.0."""

from __future__ import annotations

import psycopg

from api.export.contexte import ExportContexte, FichierExport
from api.export.excel_util import ecrire_feuille, nouveau_classeur, sauver
from api.export.modules._fichiers import fichier
from api.export.sqlutil import lignes

nom = "contacts"
dossier = "06_Contacts"


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    pid = str(ctx.projet_id)
    contacts = lignes(
        conn,
        """
        select p.nom, p.prenom, p.fonction, p.email, p.telephone, p.notes,
               p.utilisateur_id::text as utilisateur_id,
               r.libelle as role,
               s.nom as structure
          from bancarisation.projet_contact pc
          join bancarisation.personnes p on p.id = pc.personne_id
          left join bancarisation.role_contact_ref r on r.code = pc.role_code
          left join bancarisation.personnes s on s.id = p.structure_id
         where pc.projet_id = %s
         order by p.nom, p.prenom
        """,
        (pid,),
    )
    acces = lignes(
        conn,
        """
        select coalesce(o.nom, 'Personne') as organisation,
               coalesce(pa.qualite, '') as qualite,
               pa.niveau,
               pa.voir_finances
          from bancarisation.projet_acces pa
          left join bancarisation.organisations o on o.id = pa.organisation_id
         where pa.projet_id = %s
         order by o.nom
        """,
        (pid,),
    )
    wb = nouveau_classeur()
    feuille = wb.active
    feuille.title = "Contacts du projet"
    ecrire_feuille(
        feuille,
        ["Nom", "Organisation", "Fonction", "Rôle sur le projet", "Email", "Téléphone", "Compte KerERC", "Notes"],
        [
            [
                _nom(c),
                c.get("structure") or "",
                c.get("fonction") or "",
                c.get("role") or "",
                c.get("email") or "",
                c.get("telephone") or "",
                "Oui" if c.get("utilisateur_id") else "Non",
                c.get("notes") or "",
            ]
            for c in contacts
        ],
    )
    acces_ws = wb.create_sheet("Accès à l'outil")
    ecrire_feuille(
        acces_ws,
        ["Organisation", "Qualité", "Niveau", "Voit les finances"],
        [
            [a.get("organisation"), a.get("qualite"), a.get("niveau"), "Oui" if a.get("voir_finances") else "Non"]
            for a in acces
        ],
    )
    xlsx = fichier(ctx, f"{dossier}/contacts.xlsx", nom, "Contacts et accès", len(contacts))
    sauver(wb, xlsx.chemin_local)

    vcf = fichier(ctx, f"{dossier}/contacts.vcf", nom, "Carnet vCard 3.0", len(contacts))
    projet = lignes(conn, "select nom from bancarisation.projets where id = %s", (pid,))
    vcf.chemin_local.write_text(
        _vcard(contacts, (projet[0]["nom"] if projet else "Projet")),
        encoding="utf-8",
    )
    return [xlsx, vcf]


def _nom(contact: dict) -> str:
    return " ".join(p for p in (contact.get("prenom"), contact.get("nom")) if p).strip()


def _vcard(contacts: list[dict], nom_projet: str) -> str:
    import vobject

    morceaux = []
    for contact in contacts:
        carte = vobject.vCard()
        carte.add("version").value = "3.0"
        nom = contact.get("nom") or "Contact"
        prenom = contact.get("prenom") or ""
        carte.add("n").value = vobject.vcard.Name(family=nom, given=prenom)
        carte.add("fn").value = _nom(contact) or nom
        if contact.get("structure"):
            carte.add("org").value = [contact["structure"]]
        if contact.get("fonction"):
            carte.add("title").value = contact["fonction"]
        if contact.get("email"):
            carte.add("email").value = contact["email"]
        if contact.get("telephone"):
            tel = carte.add("tel")
            tel.value = contact["telephone"]
        note = f"Projet {nom_projet} – {contact.get('role') or 'contact'}"
        carte.add("note").value = note
        morceaux.append(carte.serialize())
    return "".join(morceaux)
