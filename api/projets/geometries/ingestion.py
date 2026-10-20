"""Ingestion shapefile (ZIP) → tables unités de gestion / emprise."""

from __future__ import annotations

import io
import json
import logging
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import geopandas as gpd
import psycopg
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from api.db.utilisateur import connect_utilisateur
from api.ocr.domain.ug_ids import normalize_ug_id

CoucheKind = Literal["surf", "lin", "pct", "emprise"]
CONNECT_TIMEOUT_S = 8
logger = logging.getLogger(__name__)


class GeometryIngestError(Exception):
    pass


def est_erreur_connexion(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        x in msg
        for x in (
            "connection refused",
            "could not connect",
            "connection failed",
            "injoignable",
            "timeout expired",
            "could not receive data from server",
        )
    )


def _connect():
    """Connexion PostGIS courte : évite d'attendre 60 s sur un localhost down."""
    try:
        return connect_utilisateur(connect_timeout=CONNECT_TIMEOUT_S)
    except GeometryIngestError:
        raise
    except Exception as exc:
        raise GeometryIngestError(
            "Postgres injoignable — les géométries ne peuvent pas être "
            "enregistrées (SUPABASE_HOST / DATABASE_URL). "
            f"Détail : {exc}"
        ) from exc


@dataclass
class IngestResult:
    table: str
    couche: CoucheKind
    id: str
    ug_id: str | None
    libelle: str
    geometry_type: str
    nb_features_source: int
    srid_source: int


def _find_shapefile(root: Path) -> Path:
    shp_files = sorted(root.rglob("*.shp"))
    if not shp_files:
        raise GeometryIngestError("Aucun fichier .shp trouvé dans l'archive ZIP.")
    if len(shp_files) > 1:
        # Un ZIP = une couche ; on prend le premier .shp à la racine si possible
        top = [p for p in shp_files if p.parent == root]
        return top[0] if top else shp_files[0]
    return shp_files[0]


def _extract_zip(content: bytes, dest: Path) -> Path:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            # Sécurité basique : pas de path traversal
            for info in zf.infolist():
                name = info.filename
                if name.startswith("/") or ".." in Path(name).parts:
                    raise GeometryIngestError(f"Chemin ZIP invalide: {name}")
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise GeometryIngestError("Le fichier n'est pas une archive ZIP valide.") from exc
    return _find_shapefile(dest)


def _pieces(geom: BaseGeometry, atomique: type[BaseGeometry]) -> list[BaseGeometry]:
    """Aplatit une collection et ne garde que le type demandé (polygone, ligne ou point)."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, GeometryCollection):
        out: list[BaseGeometry] = []
        for part in geom.geoms:
            out.extend(_pieces(part, atomique))
        return out
    if isinstance(geom, (MultiPolygon, MultiLineString, MultiPoint)):
        return [g for g in geom.geoms if isinstance(g, atomique) and not g.is_empty]
    if isinstance(geom, atomique):
        return [geom]
    return []


def _ensure_multi(geom: BaseGeometry, kind: CoucheKind) -> BaseGeometry:
    if geom is None or geom.is_empty:
        raise GeometryIngestError("Géométrie vide après lecture du shapefile.")

    if kind in ("surf", "emprise"):
        parts = _pieces(geom, Polygon)
        if not parts:
            raise GeometryIngestError(
                f"Aucune surface exploitable (Polygon), reçu {geom.geom_type}."
            )
        return MultiPolygon(parts)

    if kind == "lin":
        parts = _pieces(geom, LineString)
        if not parts:
            raise GeometryIngestError(
                f"Aucune ligne exploitable (LineString), reçu {geom.geom_type}."
            )
        return MultiLineString(parts)

    parts = _pieces(geom, Point)
    if not parts:
        raise GeometryIngestError(
            f"Aucun point exploitable (Point), reçu {geom.geom_type}."
        )
    return MultiPoint(parts)


def _sql_geom(kind: CoucheKind) -> str:
    """ST_MakeValid peut renvoyer une GeometryCollection : on n'en garde que la dimension utile."""
    dim = 3 if kind in ("surf", "emprise") else 2 if kind == "lin" else 1
    return (
        "ST_Multi(ST_CollectionExtract("
        "ST_MakeValid(ST_Force2D(ST_SetSRID(ST_GeomFromText(%s), %s))), "
        f"{dim}))"
    )


def _detect_kind(geom: BaseGeometry, is_emprise: bool) -> CoucheKind:
    if is_emprise:
        return "emprise"
    t = geom.geom_type
    if t in ("Polygon", "MultiPolygon"):
        return "surf"
    if t in ("LineString", "MultiLineString"):
        return "lin"
    if t in ("Point", "MultiPoint"):
        return "pct"
    if t == "GeometryCollection":
        # Décision sur le premier sous-type non vide
        for g in geom.geoms:
            if g.is_empty:
                continue
            return _detect_kind(g, is_emprise=False)
    raise GeometryIngestError(f"Type de géométrie non supporté: {t}")


def _table_for(kind: CoucheKind) -> str:
    return {
        "surf": "unites_de_gestion_surf",
        "lin": "unites_de_gestion_lin",
        "pct": "unites_de_gestion_pct",
        "emprise": "emprise_projet",
    }[kind]


def _json_safe_value(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:
            return str(v)
    if isinstance(v, float) and v != v:  # NaN
        return None
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", errors="replace")
    return v


def ingest_shapefile_zip(
    *,
    projet_id: UUID,
    file_name: str,
    content: bytes,
    ug_id: str | None,
    libelle: str,
    description: str,
    is_emprise: bool,
) -> IngestResult:
    if not content:
        raise GeometryIngestError("Fichier vide.")

    with tempfile.TemporaryDirectory(prefix="ug_shp_") as tmp:
        tmp_path = Path(tmp)
        shp_path = _extract_zip(content, tmp_path)

        try:
            gdf = gpd.read_file(shp_path)
        except Exception as exc:
            raise GeometryIngestError(f"Lecture shapefile impossible: {exc}") from exc

        if gdf.empty or gdf.geometry.isna().all():
            raise GeometryIngestError("Le shapefile ne contient aucune géométrie.")

        if gdf.crs is None:
            raise GeometryIngestError(
                "CRS absent (.prj manquant). Impossible de reprojeter en EPSG:3857."
            )

        srid_source = int(gdf.crs.to_epsg() or 0)
        if srid_source == 0:
            raise GeometryIngestError(
                f"CRS source non EPSG ({gdf.crs}). Merci de fournir un .prj EPSG."
            )

        first_geom = next(
            (g for g in gdf.geometry.values if g is not None and not g.is_empty),
            None,
        )
        if first_geom is None:
            raise GeometryIngestError("Le shapefile ne contient aucune géométrie.")

        kind = _detect_kind(first_geom, is_emprise=is_emprise)

        if kind != "emprise" and not (ug_id and ug_id.strip()):
            raise GeometryIngestError("ug_id obligatoire pour une unité de gestion.")

        ug_clean = normalize_ug_id(ug_id) if kind != "emprise" else None
        if kind != "emprise" and not ug_clean:
            raise GeometryIngestError("ug_id invalide après normalisation.")
        libelle_clean = (libelle or "").strip() or (
            "Emprise projet" if kind == "emprise" else (ug_clean or "UG")
        )
        description_clean = (description or "").strip()

        gdf_3857 = gdf.to_crs(epsg=3857)
        attr_cols = [c for c in gdf.columns if c != "geometry"]
        nb_parts = int(len(gdf))
        table = _table_for(kind)
        row_id = ""
        last_geom_type = "Unknown"

        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    for i, (_, row) in enumerate(gdf.iterrows()):
                        geom = row.geometry
                        if geom is None or geom.is_empty:
                            continue
                        feat_kind = (
                            "emprise"
                            if kind == "emprise"
                            else _detect_kind(geom, is_emprise=False)
                        )
                        multi_src = _ensure_multi(geom, feat_kind)
                        geom_3857 = gdf_3857.geometry.iloc[i]
                        if geom_3857 is None or geom_3857.is_empty:
                            continue
                        multi_3857 = _ensure_multi(geom_3857, feat_kind)
                        last_geom_type = multi_3857.geom_type

                        row_attrs: dict[str, Any] = {}
                        for k in attr_cols:
                            v = _json_safe_value(row[k])
                            if v is not None:
                                row_attrs[str(k)] = v

                        props = {
                            "_nb_features": nb_parts,
                            "index_feature": i,
                            "nb_parts": nb_parts,
                        }
                        attributs = [row_attrs] if row_attrs else []
                        feat_table = _table_for(feat_kind)

                        if feat_kind == "emprise":
                            cur.execute(
                                f"""
                                INSERT INTO bancarisation.{feat_table}
                                    (projet_id, libelle, description, geom, geom_3857,
                                     properties, attributs, source_fichier)
                                VALUES (
                                    %s, %s, %s,
                                    ST_SetSRID(ST_GeomFromText(%s), %s),
                                    ST_SetSRID(ST_GeomFromText(%s), 3857),
                                    %s::jsonb, %s::jsonb, %s
                                )
                                RETURNING id::text
                                """,
                                (
                                    str(projet_id),
                                    libelle_clean,
                                    description_clean,
                                    multi_src.wkt,
                                    srid_source,
                                    multi_3857.wkt,
                                    json.dumps(props, default=str),
                                    json.dumps(attributs, default=str),
                                    file_name,
                                ),
                            )
                        else:
                            cur.execute(
                                f"""
                                INSERT INTO bancarisation.{feat_table}
                                    (projet_id, ug_id, libelle, description, geom, geom_3857,
                                     properties, attributs, source_fichier)
                                VALUES (
                                    %s, %s, %s, %s,
                                    ST_SetSRID(ST_GeomFromText(%s), %s),
                                    ST_SetSRID(ST_GeomFromText(%s), 3857),
                                    %s::jsonb, %s::jsonb, %s
                                )
                                RETURNING id::text
                                """,
                                (
                                    str(projet_id),
                                    ug_clean,
                                    libelle_clean,
                                    description_clean,
                                    multi_src.wkt,
                                    srid_source,
                                    multi_3857.wkt,
                                    json.dumps(props, default=str),
                                    json.dumps(attributs, default=str),
                                    file_name,
                                ),
                            )
                        inserted = cur.fetchone()[0]
                        if not row_id:
                            row_id = inserted
                            table = feat_table
                            kind = feat_kind
        except GeometryIngestError:
            raise
        except Exception as exc:
            raise GeometryIngestError(f"Erreur base de données: {exc}") from exc

        if not row_id:
            raise GeometryIngestError("Aucune géométrie valide à persister.")

        return IngestResult(
            table=table,
            couche=kind,
            id=row_id,
            ug_id=ug_clean,
            libelle=libelle_clean,
            geometry_type=last_geom_type,
            nb_features_source=nb_parts,
            srid_source=srid_source,
        )


_ROLES_GEOMETRIE = {
    "unite_gestion",
    "emprise_projet",
    "plan_de_masse",
    "zone_evitement",
    "zone_reduction",
    "zone_accompagnement",
    "geometrie_complementaire",
}


_TABLES_ROLE = (
    "unites_de_gestion_surf",
    "unites_de_gestion_lin",
    "unites_de_gestion_pct",
    "emprise_projet",
)


def _tables_avec_role(cur) -> set[str]:
    """La colonne vient de la migration 054. Pas d'ALTER ici : le rôle applicatif n'est pas propriétaire."""
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'bancarisation'
          AND column_name = 'role_geometrie'
          AND table_name = ANY(%s)
        """,
        (list(_TABLES_ROLE),),
    )
    return {row[0] for row in cur.fetchall()}


def persister_entites_couche(
    *,
    projet_id: UUID,
    gdf: gpd.GeoDataFrame,
    nom_source: str,
    categorie_erc: str,
    cible: str | None,
    analyse_id: str,
    source_fichier: str,
    is_emprise: bool = False,
    affectations: dict[int, tuple[str, str]] | None = None,
    role_geometrie: str | None = None,
    attributs_saisis: dict[int, dict[str, str]] | None = None,
) -> list[str]:
    """Persiste UNE LIGNE PAR FEATURE, rattachée à la même UG (.shp).

    - Même ``ug_id`` / ``libelle`` pour toutes les parcelles du fichier
      → l'UG reste unique en navigation.
    - Géométries distinctes cliquables en carto.
    - ``attributs`` = table attributaire de CETTE feature uniquement.
    """
    if gdf.empty or gdf.geometry.isna().all():
        raise GeometryIngestError(f"Couche vide : {nom_source}")

    try:
        epsg = int(gdf.crs.to_epsg()) if gdf.crs is not None else None
    except Exception:  # noqa: BLE001
        epsg = None
    if not epsg:
        raise GeometryIngestError(
            f"CRS absent pour {nom_source} : impossible d'écrire geom_3857."
        )

    try:
        gdf_3857 = gdf.to_crs(epsg=3857)
    except Exception as exc:  # noqa: BLE001
        raise GeometryIngestError(
            f"Reprojection 3857 impossible pour {nom_source}: {exc}"
        ) from exc

    attr_cols = [c for c in gdf.columns if c != "geometry"]
    ug_clean = normalize_ug_id(nom_source) or "zone"
    libelle = nom_source
    nb_parts = int(len(gdf))
    ids: list[str] = []

    role = role_geometrie if role_geometrie in _ROLES_GEOMETRIE else None
    rattacher_ug = role in (None, "unite_gestion")

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                tables_role = _tables_avec_role(cur)
                for i, (_, row) in enumerate(gdf.iterrows()):
                    geom = row.geometry
                    if geom is None or geom.is_empty:
                        continue

                    if rattacher_ug and affectations and i in affectations:
                        code, lib = affectations[i]
                        row_ug = normalize_ug_id(code) or ug_clean
                        row_libelle = (lib or "").strip() or libelle
                        kind = _detect_kind(geom, is_emprise=False)
                    else:
                        row_ug = ug_clean if rattacher_ug else None
                        row_libelle = libelle
                        kind = (
                            "emprise" if is_emprise else _detect_kind(geom, is_emprise=False)
                        )
                    multi_src = _ensure_multi(geom, kind)
                    geom_3857 = gdf_3857.geometry.iloc[i]
                    if geom_3857 is None or geom_3857.is_empty:
                        continue
                    multi_3857 = _ensure_multi(geom_3857, kind)

                    row_attrs: dict[str, Any] = {}
                    for k in attr_cols:
                        v = _json_safe_value(row[k])
                        if v is not None:
                            row_attrs[str(k)] = v
                    for cle, valeur in (attributs_saisis or {}).get(i, {}).items():
                        texte = valeur.strip()
                        if texte:
                            row_attrs[cle] = texte
                        else:
                            row_attrs.pop(cle, None)
                    attributs = [row_attrs] if row_attrs else []

                    props = {
                        "_nb_features": nb_parts,
                        "index_feature": i,
                        "nb_parts": nb_parts,
                        "nom_source": nom_source,
                        "categorie_erc": categorie_erc,
                        "cible": cible,
                        "analyse_id": analyse_id,
                        "role_geometrie": role,
                    }
                    table = _table_for(kind)
                    colonne_role = ", role_geometrie" if table in tables_role else ""
                    valeur_role = ", %s" if table in tables_role else ""
                    params_role = (role,) if table in tables_role else ()

                    if kind == "emprise":
                        cur.execute(
                            f"""
                            INSERT INTO bancarisation.{table}
                                (projet_id, libelle, description, geom, geom_3857,
                                 properties, attributs, source_fichier{colonne_role})
                            VALUES (
                                %s, %s, %s,
                                {_sql_geom(kind)},
                                {_sql_geom(kind)},
                                %s::jsonb, %s::jsonb, %s{valeur_role}
                            )
                            RETURNING id::text
                            """,
                            (
                                str(projet_id), row_libelle, cible or "",
                                multi_src.wkt, epsg, multi_3857.wkt, 3857,
                                json.dumps(props, default=str),
                                json.dumps(attributs, default=str),
                                source_fichier,
                                *params_role,
                            ),
                        )
                    else:
                        cur.execute(
                            f"""
                            INSERT INTO bancarisation.{table}
                                (projet_id, ug_id, libelle, description, geom, geom_3857,
                                 properties, attributs, source_fichier{colonne_role})
                            VALUES (
                                %s, %s, %s, %s,
                                {_sql_geom(kind)},
                                {_sql_geom(kind)},
                                %s::jsonb, %s::jsonb, %s{valeur_role}
                            )
                            RETURNING id::text
                            """,
                            (
                                str(projet_id), row_ug, row_libelle, cible or "",
                                multi_src.wkt, epsg, multi_3857.wkt, 3857,
                                json.dumps(props, default=str),
                                json.dumps(attributs, default=str),
                                source_fichier,
                                *params_role,
                            ),
                        )
                    ids.append(cur.fetchone()[0])
    except GeometryIngestError:
        raise
    except Exception as exc:
        raise GeometryIngestError(f"Erreur base de données ({nom_source}): {exc}") from exc

    if not ids:
        raise GeometryIngestError(f"Aucune géométrie valide dans : {nom_source}")
    return ids


# Alias demandé par la spec
persister_ug = persister_entites_couche


def initialiser_foncier_apres_ingestion(projet_id: UUID) -> dict[str, Any]:
    """Après écriture des UG : IGN ∩ UG → ``parcelles`` persistées.

    N'échoue jamais l'ingestion géométrique : un incident IGN est renvoyé
    dans ``avertissements``.
    """
    try:
        from .foncier_croisement import initialiser_foncier_depuis_ugs

        out = initialiser_foncier_depuis_ugs(projet_id)
        out.setdefault("ok", True)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[foncier] initialisation après ingestion projet=%s : %s",
            projet_id,
            exc,
        )
        return {
            "ok": False,
            "nb_importees": 0,
            "nb_croisees": 0,
            "nb_liens_ug": 0,
            "parcelles": [],
            "avertissements": [str(exc)],
        }
