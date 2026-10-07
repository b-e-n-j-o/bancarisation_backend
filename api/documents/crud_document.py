import os
import re
import time
import json
from datetime import date
from typing import Any, Optional
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from supabase import Client

from api.db.supabase import get_supabase_admin
from api.db.utilisateur import connect_utilisateur

BUCKET = "documents-projet"
GEOM_TABLE = "projet_geometries"


class DocumentServiceError(Exception):
    pass


def _storage() -> Client:
    return get_supabase_admin()


def _one(sql: str, params: Any = None) -> dict[str, Any] | None:
    with connect_utilisateur(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def _all(sql: str, params: Any = None) -> list[dict[str, Any]]:
    with connect_utilisateur(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def _safe_file_name(file_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", file_name)


def _is_geojson_file(file_name: str, content_type: Optional[str]) -> bool:
    lower = file_name.lower()
    if lower.endswith(".geojson") or lower.endswith(".json"):
        return True
    if content_type:
        ctype = content_type.lower()
        if "geo+json" in ctype or ctype == "application/json":
            return True
    return False


def _parse_geojson_features(raw: bytes) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(f"GeoJSON invalide: {exc}") from exc

    if not isinstance(payload, dict):
        raise DocumentServiceError("GeoJSON invalide: objet JSON attendu.")

    gtype = payload.get("type")
    if gtype == "FeatureCollection":
        features = payload.get("features")
        if not isinstance(features, list) or len(features) == 0:
            raise DocumentServiceError("GeoJSON invalide: FeatureCollection vide.")
        return [f for f in features if isinstance(f, dict)]

    if gtype == "Feature":
        return [payload]

    raise DocumentServiceError("GeoJSON invalide: type attendu Feature ou FeatureCollection.")


def _store_geojson_features(
    projet_id: UUID,
    document_id: str,
    file_name: str,
    features: list[dict[str, Any]],
) -> None:
    rows: list[dict[str, Any]] = []
    for idx, feature in enumerate(features):
        geom = feature.get("geometry")
        if not isinstance(geom, dict):
            continue
        rows.append(
            {
                "projet_id": str(projet_id),
                "document_id": document_id,
                "nom": f"{file_name}#{idx + 1}",
                "feature_index": idx,
                "geometry_type": geom.get("type"),
                "geometry_geojson": geom,
                "properties": feature.get("properties") if isinstance(feature.get("properties"), dict) else {},
                "source_fichier": file_name,
            }
        )

    if not rows:
        raise DocumentServiceError("GeoJSON invalide: aucune géométrie exploitable.")

    try:
        with connect_utilisateur() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        f"""
                        INSERT INTO bancarisation.{GEOM_TABLE}
                            (projet_id, document_id, nom, feature_index, geometry_type,
                             geometry_geojson, properties, source_fichier)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["projet_id"],
                            row["document_id"],
                            row["nom"],
                            row["feature_index"],
                            row["geometry_type"],
                            Jsonb(row["geometry_geojson"]),
                            Jsonb(row["properties"]),
                            row["source_fichier"],
                        ),
                    )
            conn.commit()
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(
            "Impossible de stocker les géométries SIG. "
            f"Détail: {exc}"
        ) from exc


def list_documents(
    projet_id: UUID,
    occurrence_id: Optional[UUID] = None,
    *,
    only_global: bool = False,
) -> list[dict[str, Any]]:
    """Liste les documents d'un projet.

    - occurrence_id : filtre les docs liés à cette occurrence
    - only_global : docs sans occurrence (niveau projet)
    - sinon : tous les docs du projet
    """
    sql = "SELECT * FROM bancarisation.documents WHERE projet_id = %s"
    params: list[Any] = [str(projet_id)]
    if occurrence_id is not None:
        sql += " AND occurrence_id = %s"
        params.append(str(occurrence_id))
    elif only_global:
        sql += " AND occurrence_id IS NULL"
    sql += " ORDER BY categorie, created_at DESC NULLS LAST"
    try:
        return _all(sql, params)
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(f"Erreur lecture documents: {exc}") from exc


def _build_bucket_path(
    projet_id: UUID,
    file_name: str,
    occurrence_id: Optional[UUID],
    *,
    sous_dossier: Optional[str] = None,
) -> str:
    safe_name = _safe_file_name(file_name)
    ts = int(time.time() * 1000)
    if sous_dossier:
        folder = re.sub(r"[^a-zA-Z0-9._-]", "_", sous_dossier.strip("/")) or "autres"
        return f"{projet_id}/{folder}/{ts}_{safe_name}"
    if occurrence_id is not None:
        return f"{projet_id}/occurrences/{occurrence_id}/{ts}_{safe_name}"
    return f"{projet_id}/_projet/{ts}_{safe_name}"


def upload_document(
    projet_id: UUID,
    file_name: str,
    content: bytes,
    content_type: Optional[str],
    categorie: str,
    date_document: Optional[date],
    description: Optional[str],
    *,
    nom: Optional[str] = None,
    occurrence_id: Optional[UUID] = None,
    sous_dossier: Optional[str] = None,
) -> dict[str, Any]:
    client = _storage()
    geojson_features: list[dict[str, Any]] | None = None

    if categorie == "cartographie":
        if not _is_geojson_file(file_name, content_type):
            raise DocumentServiceError(
                "Pour la catégorie cartographie, seul un fichier GeoJSON (.geojson/.json) est autorisé."
            )
        geojson_features = _parse_geojson_features(content)

    niveau = _one("SELECT prive.niveau_projet(%s) AS n", (str(projet_id),))
    if not niveau or int(niveau.get("n") or 0) < 3:
        raise DocumentServiceError("Droits insuffisants pour déposer un document.")

    display_name = (nom or "").strip() or file_name
    bucket_path = _build_bucket_path(
        projet_id,
        file_name,
        occurrence_id,
        sous_dossier=sous_dossier,
    )

    try:
        client.storage.from_(BUCKET).upload(
            path=bucket_path,
            file=content,
            file_options={
                "content-type": content_type or "application/octet-stream",
                "upsert": "false",
            },
        )
        # Vérifie que l'objet est bien lisible (évite une ligne documents orpheline).
        client.storage.from_(BUCKET).download(bucket_path)
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(f"Erreur upload bucket: {exc}") from exc

    insert_payload: dict[str, Any] = {
        "projet_id": str(projet_id),
        "nom": display_name,
        "nom_fichier": file_name,
        "bucket_path": bucket_path,
        "taille_octets": len(content),
        "type_mime": content_type,
        "categorie": categorie,
        "date_document": date_document.isoformat() if date_document else None,
        "description": description or None,
    }
    insert_payload["occurrence_id"] = (
        str(occurrence_id) if occurrence_id is not None else None
    )

    try:
        row = _one(
            """
            INSERT INTO bancarisation.documents
                (projet_id, nom, nom_fichier, bucket_path, taille_octets, type_mime,
                 categorie, date_document, description, occurrence_id)
            VALUES (%(projet_id)s, %(nom)s, %(nom_fichier)s, %(bucket_path)s,
                    %(taille_octets)s, %(type_mime)s, %(categorie)s, %(date_document)s,
                    %(description)s, %(occurrence_id)s)
            RETURNING *
            """,
            insert_payload,
        )
    except Exception as exc:  # pragma: no cover
        try:
            client.storage.from_(BUCKET).remove([bucket_path])
        except Exception:
            pass
        raise DocumentServiceError(f"Erreur insertion document: {exc}") from exc

    if not row:
        raise DocumentServiceError("Insertion document échouée.")

    if categorie == "cartographie" and geojson_features is not None:
        try:
            _store_geojson_features(
                projet_id=projet_id,
                document_id=str(row.get("id")),
                file_name=file_name,
                features=geojson_features,
            )
        except Exception as exc:  # pragma: no cover
            try:
                _one("DELETE FROM bancarisation.documents WHERE id = %s RETURNING id", (str(row.get("id")),))
                client.storage.from_(BUCKET).remove([bucket_path])
            except Exception:
                pass
            raise DocumentServiceError(str(exc)) from exc

    return row


def delete_document(document_id: UUID) -> None:
    client = _storage()
    row = _one(
        "SELECT id, bucket_path FROM bancarisation.documents WHERE id = %s",
        (str(document_id),),
    )
    if not row:
        raise DocumentServiceError("Document introuvable.")

    bucket_path = row.get("bucket_path")
    deleted = _one(
        "DELETE FROM bancarisation.documents WHERE id = %s RETURNING id",
        (str(document_id),),
    )
    if not deleted:
        raise DocumentServiceError("Document introuvable.")
    if bucket_path:
        try:
            client.storage.from_(BUCKET).remove([bucket_path])
        except Exception as exc:  # pragma: no cover
            raise DocumentServiceError(f"Erreur suppression bucket: {exc}") from exc


def get_document_content(document_id: UUID) -> tuple[bytes, str, str]:
    """Télécharge le fichier via le backend (proxy) pour affichage navigateur.

    Returns:
        (content, content_type, filename)
    """
    client = _storage()
    row = _one(
        """
        SELECT id, bucket_path, type_mime, nom_fichier, nom
        FROM bancarisation.documents
        WHERE id = %s
        """,
        (str(document_id),),
    )
    if not row:
        raise DocumentServiceError("Document introuvable.")

    bucket_path = row.get("bucket_path")
    if not bucket_path:
        raise DocumentServiceError("Chemin bucket manquant.")

    try:
        raw = client.storage.from_(BUCKET).download(bucket_path)
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(f"Erreur téléchargement bucket: {exc}") from exc

    if isinstance(raw, memoryview):
        content = raw.tobytes()
    elif isinstance(raw, bytearray):
        content = bytes(raw)
    elif isinstance(raw, bytes):
        content = raw
    else:
        content = bytes(raw)

    content_type = row.get("type_mime") or "application/octet-stream"
    filename = row.get("nom_fichier") or row.get("nom") or "document"
    return content, str(content_type), str(filename)


def create_signed_url(bucket_path: str, download: Optional[str]) -> str:
    client = _storage()
    options = {"download": download} if download else None
    try:
        if options:
            result = client.storage.from_(BUCKET).create_signed_url(
                path=bucket_path,
                expires_in=3600,
                options=options,
            )
        else:
            result = client.storage.from_(BUCKET).create_signed_url(
                path=bucket_path,
                expires_in=3600,
            )
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(f"Erreur URL signée: {exc}") from exc

    def _normalize_url(url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        base = os.getenv("SUPABASE_URL", "").rstrip("/")
        if not base:
            return url
        if url.startswith("/"):
            return f"{base}{url}"
        return f"{base}/{url}"

    # Cas dictionnaire (selon versions de supabase-py/storage3)
    if isinstance(result, dict):
        signed = result.get("signedURL") or result.get("signedUrl")
        if signed:
            return _normalize_url(str(signed))
        data = result.get("data")
        if isinstance(data, dict):
            nested = data.get("signedURL") or data.get("signedUrl")
            if nested:
                return _normalize_url(str(nested))

    # Cas objet avec attributs
    for attr in ("signedURL", "signedUrl", "signed_url"):
        value = getattr(result, attr, None)
        if value:
            return _normalize_url(str(value))

    raise DocumentServiceError("URL signée introuvable dans la réponse Supabase.")


def create_signed_url_for_document(document_id: UUID, download: Optional[str] = None) -> str:
    """Signe le chemin stocké sur la ligne, jamais un chemin fourni par le client.

    Le SELECT passe par ``connect_utilisateur`` : la RLS de ``documents``
    masque une ligne que l'appelant ne peut pas lire, et aucune URL n'est émise.
    """
    row = _one(
        "SELECT id, bucket_path FROM bancarisation.documents WHERE id = %s",
        (str(document_id),),
    )
    if not row or not row.get("bucket_path"):
        raise DocumentServiceError("Document introuvable.")
    return create_signed_url(str(row["bucket_path"]), download)
