"""Ingestion shapefile (ZIP) → tables unités de gestion / emprise."""

from __future__ import annotations

import io
import json
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
from shapely.ops import unary_union

from api.ocr.db_env import get_database_url
from api.ocr.ug_ids import normalize_ug_id

CoucheKind = Literal["surf", "lin", "pct", "emprise"]


class GeometryIngestError(Exception):
    pass


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


def _ensure_multi(geom: BaseGeometry, kind: CoucheKind) -> BaseGeometry:
    if geom is None or geom.is_empty:
        raise GeometryIngestError("Géométrie vide après lecture du shapefile.")

    if isinstance(geom, GeometryCollection):
        parts = [g for g in geom.geoms if not g.is_empty]
        if not parts:
            raise GeometryIngestError("GeometryCollection vide.")
        geom = unary_union(parts)

    if kind in ("surf", "emprise"):
        if isinstance(geom, Polygon):
            return MultiPolygon([geom])
        if isinstance(geom, MultiPolygon):
            return geom
        raise GeometryIngestError(
            f"Emprise / UG surfacique attendue (Polygon/MultiPolygon), reçu {geom.geom_type}."
        )

    if kind == "lin":
        if isinstance(geom, LineString):
            return MultiLineString([geom])
        if isinstance(geom, MultiLineString):
            return geom
        raise GeometryIngestError(
            f"UG linéaire attendue (LineString/MultiLineString), reçu {geom.geom_type}."
        )

    # pct
    if isinstance(geom, Point):
        return MultiPoint([geom])
    if isinstance(geom, MultiPoint):
        return geom
    raise GeometryIngestError(
        f"UG ponctuelle attendue (Point/MultiPoint), reçu {geom.geom_type}."
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


def _attrs_properties(gdf: gpd.GeoDataFrame) -> dict[str, Any]:
    cols = [c for c in gdf.columns if c != "geometry"]
    if not cols:
        return {}
    # Première feature + résumé
    first = gdf.iloc[0][cols].to_dict()
    cleaned: dict[str, Any] = {}
    for k, v in first.items():
        if hasattr(v, "item"):
            try:
                v = v.item()
            except Exception:
                v = str(v)
        if isinstance(v, float) and v != v:  # NaN
            continue
        cleaned[str(k)] = v
    cleaned["_nb_features"] = int(len(gdf))
    return cleaned


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
            # CRS nommé sans code EPSG — on force via to_crs après WKT
            raise GeometryIngestError(
                f"CRS source non EPSG ({gdf.crs}). Merci de fournir un .prj EPSG."
            )

        merged = unary_union(gdf.geometry.values)
        kind = _detect_kind(merged, is_emprise=is_emprise)

        if kind != "emprise" and not (ug_id and ug_id.strip()):
            raise GeometryIngestError("ug_id obligatoire pour une unité de gestion.")

        ug_clean = normalize_ug_id(ug_id) if kind != "emprise" else None
        if kind != "emprise" and not ug_clean:
            raise GeometryIngestError("ug_id invalide après normalisation.")
        libelle_clean = (libelle or "").strip() or (
            "Emprise projet" if kind == "emprise" else (ug_clean or "UG")
        )
        description_clean = (description or "").strip()

        multi_src = _ensure_multi(merged, kind)
        gdf_3857 = gdf.to_crs(epsg=3857)
        multi_3857 = _ensure_multi(unary_union(gdf_3857.geometry.values), kind)

        props = _attrs_properties(gdf)
        table = _table_for(kind)

        try:
            with psycopg.connect(get_database_url()) as conn:
                with conn.cursor() as cur:
                    if kind == "emprise":
                        cur.execute(
                            f"""
                            INSERT INTO bancarisation.{table}
                                (projet_id, libelle, description, geom, geom_3857, properties, source_fichier)
                            VALUES (
                                %s, %s, %s,
                                ST_SetSRID(ST_GeomFromText(%s), %s),
                                ST_SetSRID(ST_GeomFromText(%s), 3857),
                                %s::jsonb,
                                %s
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
                                file_name,
                            ),
                        )
                    else:
                        cur.execute(
                            f"""
                            INSERT INTO bancarisation.{table}
                                (projet_id, ug_id, libelle, description, geom, geom_3857, properties, source_fichier)
                            VALUES (
                                %s, %s, %s, %s,
                                ST_SetSRID(ST_GeomFromText(%s), %s),
                                ST_SetSRID(ST_GeomFromText(%s), 3857),
                                %s::jsonb,
                                %s
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
                                file_name,
                            ),
                        )
                    row_id = cur.fetchone()[0]
                conn.commit()
        except GeometryIngestError:
            raise
        except Exception as exc:
            raise GeometryIngestError(f"Erreur base de données: {exc}") from exc

        return IngestResult(
            table=table,
            couche=kind,
            id=row_id,
            ug_id=ug_clean,
            libelle=libelle_clean,
            geometry_type=multi_3857.geom_type,
            nb_features_source=len(gdf),
            srid_source=srid_source,
        )
