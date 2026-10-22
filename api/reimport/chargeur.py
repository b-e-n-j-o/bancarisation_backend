"""Création du projet à partir des CSV, dans une seule transaction."""

from __future__ import annotations

import csv
import logging
import uuid
from pathlib import Path

from psycopg import sql

from api.db.supabase import get_supabase_admin
from api.export.contexte import connect_backend
from api.reimport import COLONNES_AUTEUR, COLONNES_IGNOREES, TABLES_IMPORT
from api.reimport.analyse import ArchiveExport

logger = logging.getLogger("api.reimport.chargeur")
BUCKET_DOCUMENTS = "documents-projet"


def importer(
    archive: ArchiveExport,
    organisation_id: str,
    user_claims: dict,
    import_id: str,
    jeu=None,
) -> tuple[str, list[str]]:
    """Retourne l'id du projet créé et les avertissements. Lève en cas d'écart."""
    from api.db.utilisateur import connect_utilisateur
    from psycopg.rows import dict_row

    envoyes: list[str] = []
    try:
        with connect_utilisateur(user_claims, row_factory=dict_row) as conn:
            conn.execute("select set_config('kererc.import_en_cours', 'on', true)")
            correspondance: dict[tuple[str, str], str] = {}
            comptages: dict[str, int] = {}
            avertissements: list[str] = []
            fichiers = jeu.fichiers if jeu is not None else _index_fichiers(archive)
            colonnes = _colonnes(conn)
            conserver = bool(jeu and jeu.conserver_ids)
            for table, fichier, fks, tableaux in TABLES_IMPORT:
                chemin = None if jeu is not None else archive.racine / "data" / fichier
                lignes = jeu.tables.get(table) if jeu is not None else None
                if jeu is None and (chemin is None or not chemin.is_file()):
                    continue
                if jeu is not None and not lignes:
                    continue
                n = _charger_table(
                    conn, archive, table, chemin, fks, tableaux, correspondance,
                    organisation_id, colonnes.get(table, {}), fichiers, envoyes, avertissements,
                    lignes=lignes, conserver_ids=conserver,
                )
                comptages[table] = n
            projet_id = next((v for (t, _a), v in correspondance.items() if t == "projets"), None)
            if not projet_id:
                raise RuntimeError("Aucun projet dans l'archive.")
            for table, n in comptages.items():
                if "projet_id" not in colonnes.get(table, {}):
                    continue
                reel = conn.execute(
                    sql.SQL("select count(*)::int as n from bancarisation.{} where projet_id = %s").format(
                        sql.Identifier(table)
                    ),
                    (projet_id,),
                ).fetchone()
                if int(reel["n"]) != n:
                    raise RuntimeError(f"Écart de comptage sur {table} : archive {n}, base {reel['n']}.")
        _enregistrer_map(import_id, correspondance)
        return projet_id, avertissements
    except Exception:
        _retirer_fichiers(envoyes)
        raise


def _personne_existante(conn, organisation_id: str, email) -> str | None:
    adresse = str(email or "").strip()
    if not adresse:
        return None
    trouve = conn.execute(
        """
        select id::text from bancarisation.personnes
         where organisation_id = %s
           and lower(email) = lower(%s)
           and not archive
         limit 1
        """,
        (organisation_id, adresse),
    ).fetchone()
    return trouve["id"] if trouve else None


def _index_fichiers(archive: ArchiveExport) -> dict[str, dict[str, str]]:
    chemin = archive.racine / "data" / "_fichiers.csv"
    with chemin.open(encoding="utf-8", newline="") as fichier:
        return {ligne["document_id"]: ligne for ligne in csv.DictReader(fichier)}


def _colonnes(conn) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        """
        select table_name, column_name, udt_name, is_nullable
          from information_schema.columns
         where table_schema = 'bancarisation'
        """
    ).fetchall()
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        out.setdefault(row["table_name"], {})[row["column_name"]] = row["udt_name"]
    return out


def _charger_table(
    conn, archive, table, chemin: Path | None, fks, tableaux, correspondance,
    organisation_id, types: dict[str, str], fichiers, envoyes, avertissements,
    lignes: list[dict] | None = None,
    conserver_ids: bool = False,
) -> int:
    if lignes is None:
        if chemin is None or not chemin.is_file():
            return 0
        with chemin.open(encoding="utf-8", newline="") as fichier:
            lignes = list(csv.DictReader(fichier))
    n = 0
    for brut in lignes:
        ancien = (brut.get("id") or "").strip()
        nouvel_id = ancien if conserver_ids and ancien else str(uuid.uuid4())
        if ancien:
            correspondance[(table, ancien)] = nouvel_id
        if table == "personnes":
            repris = _personne_existante(conn, organisation_id, brut.get("email"))
            if repris and ancien:
                correspondance[(table, ancien)] = repris
                avertissements.append(
                    f"Contact {brut.get('email')} déjà présent dans l'organisation : fiche réutilisée."
                )
                continue
        colonnes: list[str] = []
        valeurs: list = []
        expressions: list[sql.Composable] = []
        if "id" in types and not types["id"].startswith("int") and types["id"] not in {"int4", "int8", "bigserial"}:
            colonnes.append("id")
            valeurs.append(nouvel_id)
            expressions.append(sql.Placeholder())
        for cle, valeur in brut.items():
            if cle in COLONNES_IGNOREES or cle in {"id"}:
                continue
            if cle.endswith("_wkt"):
                if valeur:
                    colonnes.append(cle[:-4])
                    valeurs.append(valeur)
                    expressions.append(sql.SQL("ST_GeomFromText(%s, 2154)"))
                continue
            if cle not in types:
                continue
            if cle in COLONNES_AUTEUR:
                colonnes.append(cle)
                valeurs.append(None)
                expressions.append(sql.Placeholder())
                if valeur:
                    avertissements.append(f"{table}.{cle} remis à vide (compte de l'instance source).")
                continue
            if cle == "organisation_id":
                colonnes.append(cle)
                valeurs.append(organisation_id)
                expressions.append(sql.Placeholder())
                continue
            if cle == "prestataire_id" and valeur:
                nom = brut.get("prestataire") or ""
                trouve = conn.execute(
                    "select id::text from bancarisation.prestataires where nom = %s limit 1",
                    (nom,),
                ).fetchone()
                colonnes.append(cle)
                valeurs.append(trouve["id"] if trouve else None)
                expressions.append(sql.Placeholder())
                if not trouve:
                    avertissements.append(f"Prestataire « {nom or valeur} » introuvable : lien retiré.")
                continue
            if cle in fks and valeur:
                valeur = correspondance.get((fks[cle], valeur), None)
            if cle in tableaux and valeur:
                valeur = _remapper_tableau(valeur, fks.get("documents", "documents"), correspondance)
            if cle == "bucket_path" and table == "documents" and ancien in fichiers:
                valeur = _envoyer_document(archive, fichiers[ancien], organisation_id, nouvel_id, envoyes)
            if valeur == "":
                valeur = None
            if isinstance(valeur, (dict, list)) and types[cle] == "jsonb":
                import json
                valeur = json.dumps(valeur, ensure_ascii=False)
            colonnes.append(cle)
            valeurs.append(valeur)
            udt = types[cle]
            if udt.startswith("_"):
                expressions.append(sql.SQL("{}::{}[]").format(sql.Placeholder(), sql.SQL(udt[1:])))
            elif udt == "jsonb":
                expressions.append(sql.SQL("%s::jsonb"))
            else:
                expressions.append(sql.Placeholder())
        if table == "occurrence" and "prestataire_id" not in colonnes and "prestataire_id" in types:
            nom = str(brut.get("prestataire") or "").strip()
            if nom:
                trouve = conn.execute(
                    "select id::text from bancarisation.prestataires where nom = %s limit 1",
                    (nom,),
                ).fetchone()
                if trouve:
                    colonnes.append("prestataire_id")
                    valeurs.append(trouve["id"])
                    expressions.append(sql.Placeholder())
                else:
                    message = f"Prestataire « {nom} » introuvable : le nom est conservé, sans lien d'annuaire."
                    if message not in avertissements:
                        avertissements.append(message)
        if "geom" in colonnes and "geom_3857" in types and "geom_3857" not in colonnes:
            wkt = valeurs[colonnes.index("geom")]
            colonnes.append("geom_3857")
            valeurs.append(wkt)
            expressions.append(sql.SQL("ST_Transform(ST_GeomFromText(%s, 2154), 3857)"))
        if not colonnes:
            continue
        requete = sql.SQL("insert into bancarisation.{} ({}) values ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(c) for c in colonnes),
            sql.SQL(", ").join(expressions),
        )
        conn.execute(requete, valeurs)
        n += 1
    return n


def _remapper_tableau(valeur: str, table: str, correspondance: dict) -> str | None:
    corps = valeur.strip().strip("{}")
    if not corps:
        return "{}"
    ids = []
    for morceau in corps.split(","):
        ancien = morceau.strip().strip('"')
        ids.append(correspondance.get((table, ancien), ancien))
    return "{" + ",".join(ids) + "}"


def _envoyer_document(archive, meta: dict, organisation_id: str, nouvel_id: str, envoyes: list[str]) -> str:
    local = meta.get("chemin_local")
    if local:
        contenu = Path(local).read_bytes()
        nom = Path(meta.get("nom") or local).name
    else:
        chemin = meta["chemin_archive"]
        contenu = archive.lire(chemin)
        nom = Path(chemin).name
    bucket_path = f"{organisation_id}/{nouvel_id}/{nom}"
    client = get_supabase_admin()
    client.storage.from_(BUCKET_DOCUMENTS).upload(
        bucket_path,
        contenu,
        file_options={"content-type": "application/octet-stream", "upsert": "true"},
    )
    envoyes.append(bucket_path)
    return bucket_path


def _retirer_fichiers(chemins: list[str]) -> None:
    if not chemins:
        return
    try:
        get_supabase_admin().storage.from_(BUCKET_DOCUMENTS).remove(chemins)
    except Exception:
        logger.exception("Nettoyage storage après échec d'import")


def _enregistrer_map(import_id: str, correspondance: dict[tuple[str, str], str]) -> None:
    with connect_backend() as conn:
        for (table, ancien), nouveau in correspondance.items():
            conn.execute(
                """
                insert into bancarisation.import_projet_id_map
                  (import_id, table_nom, ancien_id, nouvel_id)
                values (%s, %s, %s, %s)
                on conflict do nothing
                """,
                (import_id, table, ancien, nouveau),
            )


def marquer(import_id: str, **champs) -> None:
    from psycopg.types.json import Jsonb

    sets = []
    valeurs = []
    for cle, valeur in champs.items():
        sets.append(f"{cle} = %s")
        if cle in {"rapport", "manifest_source", "parametres"} and not isinstance(valeur, Jsonb):
            valeurs.append(Jsonb(valeur))
        else:
            valeurs.append(valeur)
    valeurs.append(import_id)
    with connect_backend() as conn:
        conn.execute(
            f"update bancarisation.import_projet set {', '.join(sets)} where id = %s",
            valeurs,
        )
