import os
import re
import time
import json
from datetime import date
from typing import Any, Optional
from uuid import UUID

from dotenv import load_dotenv
from supabase import Client, create_client


load_dotenv()

BUCKET = "documents-projets"
GEOM_TABLE = "projet_geometries"


class DocumentServiceError(Exception):
    pass


def _get_supabase_client() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not service_key:
        raise DocumentServiceError(
            "Variables SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY manquantes."
        )

    return create_client(supabase_url, service_key)


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
    client: Client,
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
        client.schema("bancarisation").table(GEOM_TABLE).insert(rows).execute()
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(
            "Impossible de stocker les géométries SIG. "
            "Crée la table bancarisation.projet_geometries (voir script SQL). "
            f"Détail: {exc}"
        ) from exc


def list_documents(projet_id: UUID) -> list[dict[str, Any]]:
    client = _get_supabase_client()
    try:
        response = (
            client.schema("bancarisation")
            .table("documents")
            .select("*")
            .eq("projet_id", str(projet_id))
            .order("categorie")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(f"Erreur Supabase: {exc}") from exc

    data = response.data or []
    return data if isinstance(data, list) else [data]


def upload_document(
    projet_id: UUID,
    file_name: str,
    content: bytes,
    content_type: Optional[str],
    categorie: str,
    date_document: Optional[date],
    description: Optional[str],
) -> dict[str, Any]:
    client = _get_supabase_client()
    geojson_features: list[dict[str, Any]] | None = None

    if categorie == "cartographie":
        if not _is_geojson_file(file_name, content_type):
            raise DocumentServiceError(
                "Pour la catégorie cartographie, seul un fichier GeoJSON (.geojson/.json) est autorisé."
            )
        geojson_features = _parse_geojson_features(content)

    safe_name = _safe_file_name(file_name)
    bucket_path = f"{projet_id}/{categorie}/{int(time.time() * 1000)}_{safe_name}"

    try:
        client.storage.from_(BUCKET).upload(
            path=bucket_path,
            file=content,
            file_options={
                "content-type": content_type or "application/octet-stream",
                "upsert": "false",
            },
        )
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(f"Erreur upload bucket: {exc}") from exc

    insert_payload = {
        "projet_id": str(projet_id),
        "nom": file_name,
        "nom_fichier": file_name,
        "bucket_path": bucket_path,
        "taille_octets": len(content),
        "type_mime": content_type,
        "categorie": categorie,
        "date_document": date_document.isoformat() if date_document else None,
        "description": description or None,
    }

    try:
        response = (
            client.schema("bancarisation")
            .table("documents")
            .insert(insert_payload, returning="representation")
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        try:
            client.storage.from_(BUCKET).remove([bucket_path])
        except Exception:
            pass
        raise DocumentServiceError(f"Erreur Supabase: {exc}") from exc

    data = response.data
    row = data[0] if isinstance(data, list) and data else data
    if not row:
        raise DocumentServiceError("Insertion document échouée.")

    if categorie == "cartographie" and geojson_features is not None:
        try:
            _store_geojson_features(
                client=client,
                projet_id=projet_id,
                document_id=str(row.get("id")),
                file_name=file_name,
                features=geojson_features,
            )
        except Exception as exc:  # pragma: no cover
            try:
                client.schema("bancarisation").table("documents").delete().eq("id", str(row.get("id"))).execute()
                client.storage.from_(BUCKET).remove([bucket_path])
            except Exception:
                pass
            raise DocumentServiceError(str(exc)) from exc

    return row


def delete_document(document_id: UUID) -> None:
    client = _get_supabase_client()

    try:
        lookup = (
            client.schema("bancarisation")
            .table("documents")
            .select("id,bucket_path")
            .eq("id", str(document_id))
            .maybe_single()
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(f"Erreur Supabase: {exc}") from exc

    row = lookup.data
    if not row:
        raise DocumentServiceError("Document introuvable.")

    bucket_path = row.get("bucket_path")
    if bucket_path:
        try:
            client.storage.from_(BUCKET).remove([bucket_path])
        except Exception as exc:  # pragma: no cover
            raise DocumentServiceError(f"Erreur suppression bucket: {exc}") from exc

    try:
        (
            client.schema("bancarisation")
            .table("documents")
            .delete()
            .eq("id", str(document_id))
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise DocumentServiceError(f"Erreur suppression base: {exc}") from exc


def create_signed_url(bucket_path: str, download: Optional[str]) -> str:
    client = _get_supabase_client()
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
