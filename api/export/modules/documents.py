"""05_Documents : copie des binaires et index. Les chemins viennent d'une lecture sous RLS."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx
import psycopg

from api.db.supabase import get_supabase_admin
from api.export.contexte import ExportContexte, FichierExport
from api.export.excel_util import ecrire_feuille, nouveau_classeur, sauver
from api.export.modules._fichiers import fichier
from api.export.nommage import slug
from api.export.sqlutil import lignes

nom = "documents"
dossier = "05_Documents"
BUCKET = "documents-projet"

DOSSIERS_CATEGORIE = {
    "arrete": "Arretes",
    "plan_gestion": "Plans-de-gestion",
    "suivi_travaux": "Suivi-de-travaux",
    "cerfa": "Cerfa",
    "sig": "Sig",
    "bilans_annuels": "Bilans-annuels",
    "reglementaire": "Reglementaire",
    "foncier": "Foncier",
    "technique": "Technique",
    "suivi": "Suivi",
    "financier": "Financier",
    "cartographie": "Cartographie",
    "photos": "Photos",
    "correspondances": "Correspondances",
}


class DocumentAbsent(Exception):
    pass


@dataclass
class LigneDocument:
    id: str
    nom: str
    nom_fichier: str | None
    bucket_path: str | None
    categorie: str | None
    date_document: object
    created_at: object
    taille_octets: int | None
    deposant: str | None
    occurrence_id: str | None
    liens: str
    echange: bool = False


Telechargeur = Callable[[str, Path], str]


def produire(ctx: ExportContexte, conn: psycopg.Connection) -> list[FichierExport]:
    lignes_doc = _lire(conn, str(ctx.projet_id))
    return deposer_documents(ctx, lignes_doc, telecharger_binaire, ctx.options.inclure_documents)


def deposer_documents(
    ctx: ExportContexte,
    lignes_doc: list[LigneDocument],
    telecharger: Telechargeur,
    inclure: bool,
) -> list[FichierExport]:
    index_lignes = []
    produits: list[FichierExport] = []
    for doc in lignes_doc:
        annee = _annee(doc)
        dossier_cat = _dossier(doc)
        ext = Path(doc.nom_fichier or doc.nom or "document").suffix.lower()
        if not ext or not ext.isascii():
            ext = ""
        nom_slug = slug(Path(doc.nom_fichier or doc.nom or "document").stem, 50)
        jour = _jour(doc)
        relatif = f"{dossier}/{dossier_cat}/{annee}/{jour}_{nom_slug}{ext}"
        statut_fichier = "inclus"
        sha = ""
        if not inclure:
            statut_fichier = "non inclus dans l'export"
        elif not doc.bucket_path:
            statut_fichier = "fichier manquant"
            ctx.avertir(f"Document sans chemin de stockage : {doc.nom}")
        else:
            dest = fichier(ctx, relatif, nom, doc.nom or "Document", compresser=_compressible(ext))
            try:
                sha = telecharger(doc.bucket_path, dest.chemin_local)
                produits.append(dest)
            except DocumentAbsent as exc:
                statut_fichier = "fichier manquant"
                ctx.avertir(f"Fichier manquant ({doc.nom}) : {exc}")
                if dest.chemin_local.exists():
                    dest.chemin_local.unlink()
        index_lignes.append([
            relatif if statut_fichier == "inclus" else "",
            doc.nom,
            doc.categorie or "",
            annee,
            doc.created_at,
            doc.deposant or "",
            doc.liens,
            doc.taille_octets,
            sha,
            statut_fichier,
        ])
    wb = nouveau_classeur()
    ws = wb.active
    ws.title = "Index"
    ecrire_feuille(
        ws,
        [
            "Chemin dans l'export", "Nom d'origine", "Catégorie", "Année",
            "Date de dépôt", "Déposant", "Liens", "Taille", "SHA-256", "Statut",
        ],
        index_lignes,
        colonnes_date=(5,),
    )
    if inclure:
        for row in ws.iter_rows(min_row=2, max_col=1):
            cellule = row[0]
            if cellule.value:
                cellule.hyperlink = str(cellule.value)
                cellule.style = "Hyperlink"
    fic = fichier(ctx, f"{dossier}/index_documents.xlsx", nom, "Index des documents", len(index_lignes))
    sauver(wb, fic.chemin_local)
    produits.append(fic)
    return produits


def _lire(conn: psycopg.Connection, pid: str) -> list[LigneDocument]:
    docs = lignes(
        conn,
        """
        select d.id::text, d.nom, d.nom_fichier, d.bucket_path, d.categorie,
               d.date_document, d.created_at, d.taille_octets, d.occurrence_id::text,
               o.code as occurrence_code, o.titre as occurrence_titre
          from bancarisation.documents d
          left join bancarisation.occurrence o on o.id = d.occurrence_id
         where d.projet_id = %s
         order by d.created_at
        """,
        (pid,),
    )
    echanges = _ids_echanges(conn, pid)
    droits = _liens_droits(conn, pid)
    sortie = []
    for doc in docs:
        liens = []
        if doc.get("occurrence_code") or doc.get("occurrence_titre"):
            liens.append(" ".join(p for p in (doc.get("occurrence_code"), doc.get("occurrence_titre")) if p))
        if doc["id"] in droits:
            liens.append(droits[doc["id"]])
        if doc["id"] in echanges:
            liens.append("Échange")
        doc["_echange"] = doc["id"] in echanges
        sortie.append(LigneDocument(
            id=doc["id"],
            nom=doc.get("nom") or doc.get("nom_fichier") or "document",
            nom_fichier=doc.get("nom_fichier"),
            bucket_path=doc.get("bucket_path"),
            categorie=doc.get("categorie"),
            date_document=doc.get("date_document"),
            created_at=doc.get("created_at"),
            taille_octets=doc.get("taille_octets"),
            deposant=None,
            occurrence_id=doc.get("occurrence_id"),
            liens=" ; ".join(liens),
            echange=bool(doc["_echange"]),
        ))
    return sortie


def _ids_echanges(conn, pid: str) -> set[str]:
    try:
        with conn.transaction():
            rows = lignes(
                conn,
                """
                select unnest(m.documents_ids)::text as id
                  from bancarisation.demande_message m
                  join bancarisation.demande d on d.id = m.demande_id
                 where d.projet_id = %s
                """,
                (pid,),
            )
    except Exception:
        return set()
    return {r["id"] for r in rows if r.get("id")}


def _liens_droits(conn, pid: str) -> dict[str, str]:
    try:
        with conn.transaction():
            rows = lignes(
                conn,
                """
                select dd.document_id::text as id, df.nature
                  from bancarisation.droit_documents dd
                  join bancarisation.droits_fonciers df on df.id = dd.droit_id
                  join bancarisation.parcelles pa on pa.id = df.parcelle_id
                 where pa.projet_id = %s
                """,
                (pid,),
            )
    except Exception:
        return {}
    return {r["id"]: f"Droit {r.get('nature') or ''}".strip() for r in rows}


def _dossier(doc: LigneDocument) -> str:
    if doc.echange:
        return "Echanges"
    categorie = doc.categorie or ""
    if categorie == "foncier":
        return "Foncier"
    return DOSSIERS_CATEGORIE.get(categorie, "Non-classes")


def _annee(doc: LigneDocument) -> str:
    for valeur in (doc.date_document, doc.created_at):
        if isinstance(valeur, datetime):
            return str(valeur.year)
        if hasattr(valeur, "year"):
            return str(valeur.year)
    return "sans-date"


def _jour(doc: LigneDocument) -> str:
    for valeur in (doc.date_document, doc.created_at):
        if isinstance(valeur, datetime):
            return valeur.date().isoformat()
        if hasattr(valeur, "isoformat") and not isinstance(valeur, datetime):
            return valeur.isoformat()[:10]
    return "0000-00-00"


def _compressible(ext: str) -> bool:
    return ext not in {".pdf", ".png", ".jpg", ".jpeg", ".zip", ".gpkg"}


def telecharger_binaire(bucket_path: str, cible: Path) -> str:
    """Télécharge un objet dont le chemin a été obtenu sous RLS. Ne liste pas le bucket."""
    client = get_supabase_admin()
    try:
        resultat = client.storage.from_(BUCKET).create_signed_url(bucket_path, 300)
    except Exception as exc:
        raise DocumentAbsent(str(exc)) from exc
    url = _url(resultat)
    hachage = hashlib.sha256()
    cible.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as http:
            with http.stream("GET", url) as reponse:
                if reponse.status_code >= 400:
                    raise DocumentAbsent(f"HTTP {reponse.status_code}")
                with cible.open("wb") as fichier_local:
                    for bloc in reponse.iter_bytes():
                        hachage.update(bloc)
                        fichier_local.write(bloc)
    except DocumentAbsent:
        raise
    except Exception as exc:
        raise DocumentAbsent(str(exc)) from exc
    return hachage.hexdigest()


def _url(resultat: object) -> str:
    url = ""
    if isinstance(resultat, dict):
        url = str(resultat.get("signedURL") or resultat.get("signedUrl") or "")
        data = resultat.get("data")
        if not url and isinstance(data, dict):
            url = str(data.get("signedURL") or data.get("signedUrl") or "")
    if not url:
        url = str(getattr(resultat, "signed_url", "") or getattr(resultat, "signedURL", "") or "")
    if not url:
        raise DocumentAbsent("URL signée vide")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    return f"{base}{url}" if url.startswith("/") else f"{base}/{url}"
