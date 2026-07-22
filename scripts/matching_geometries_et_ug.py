#!/usr/bin/env python3
"""Associe les géométries SHP aux UG surfaciques — batch projet ↔ fichier.

Connexion : ``api.db.env.get_database_url`` (``backend/.env``).

Usage (depuis ``backend/``) ::

    # Tous les projets du mapping (une passe)
    python scripts/matching_geometries_et_ug.py

    # Un seul couple
    python scripts/matching_geometries_et_ug.py \\
        --projet 23446450-ab89-45cf-ad73-e3956974988f

    # Dossier SHP custom + pas de mélange
    python scripts/matching_geometries_et_ug.py --shp-dir /chemin/vers/PROJETS_MOCKED --no-shuffle
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from uuid import UUID

import geopandas as gpd
import psycopg
from psycopg.rows import dict_row

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from api.db.env import get_database_url  # noqa: E402

DEFAULT_SHP_DIR = (
    _REPO / "DATA" / "UNITES_DE_GESTION" / "PROJETS_MOCKED"
)

# Matching projet_id → nom du shapefile (dans --shp-dir)
MAPPING_PROJETS_SHP: dict[str, str] = {
    "457e043b-4b56-44e0-a635-3a9c5e5fdc07": "aire-sur-l-adoure.shp",
    "c6c1da28-2baf-4962-aa36-5c281a9abb07": "cambo-les-bains.shp",
    "fd99a6f4-820f-4e7a-b426-ee794df28071": "hourtin.shp",
    "73eb10c5-64e7-4c3f-9bdf-124c4ee0a537": "la-grasse.shp",
    "4c051b0e-632a-4311-b91b-a03e61ce37b3": "la-teste-de-buch.shp",
    "4258ea58-b921-485f-9c6a-a8f177f2df0e": "les-eyzies.shp",
    "e57aa9f2-380f-470c-b41d-1962ab0fa0ee": "marmande.shp",
    "603651ba-a020-49a8-8bc1-58da57de554d": "mimizan.shp",
    "7cfc948d-53e9-408a-aaac-256dd403dfdb": "oloron-sainte-marie.shp",
    "30ccbdc1-1208-4f69-9a2a-e07350dd833a": "prades.shp",
    "23446450-ab89-45cf-ad73-e3956974988f": "sabres.shp",
    "ad8a1485-a761-4a60-a98f-29140d687462": "saint-jean-dangely.shp",
    "94f9c1cf-1fb5-4fc9-97da-0ee6240e2819": "salles.shp",
    "51ec68a0-283e-40ba-9f31-de56afc42a80": "salses-le-chateau.shp",
    "723644ba-daad-4daf-abc0-b59fda70e6ea": "viilleneuve-sur-lot.shp",
    "e5ab8143-cdb7-4327-9433-5418b1a0634c": "villandraut.shp",
}


def assign_shp_to_project_ug(
    projet_id: UUID,
    shp_path: Path,
    *,
    shuffle: bool = True,
    conn: psycopg.Connection | None = None,
) -> None:
    """Associe les géométries d'un SHP aux UG surf d'un projet en BDD."""
    if not shp_path.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {shp_path}")

    gdf = gpd.read_file(shp_path)
    file_name = shp_path.name

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=2154)

    gdf_4326 = gdf.to_crs(epsg=4326)
    gdf_3857 = gdf.to_crs(epsg=3857)

    geom_pairs = [
        (g4326.wkt, g3857.wkt)
        for g4326, g3857 in zip(gdf_4326.geometry, gdf_3857.geometry, strict=False)
        if g4326 is not None and not g4326.is_empty
    ]

    if shuffle:
        random.shuffle(geom_pairs)

    own_conn = conn is None
    if own_conn:
        conn = psycopg.connect(get_database_url(), row_factory=dict_row)

    assert conn is not None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ug_id, libelle
                FROM bancarisation.unites_de_gestion_surf
                WHERE projet_id = %s
                ORDER BY ug_id ASC
                """,
                (str(projet_id),),
            )
            ug_rows = cur.fetchall()

            if not ug_rows:
                print(f"  ⚠️  Aucune UG surf en BDD pour le projet {projet_id}")
                return

            print(
                f"  📌 {len(ug_rows)} UG(s), {len(geom_pairs)} géométrie(s) "
                f"dans {file_name}"
            )

            update_query = """
                UPDATE bancarisation.unites_de_gestion_surf
                SET
                    geom = ST_Multi(ST_Force2D(ST_GeomFromText(%s, 4326))),
                    geom_3857 = ST_Multi(ST_Force2D(ST_GeomFromText(%s, 3857))),
                    source_fichier = %s,
                    updated_at = NOW()
                WHERE id = %s
            """

            for i, ug in enumerate(ug_rows):
                if i >= len(geom_pairs):
                    print(
                        f"  ⚠️  Plus d'UG que de géométries — arrêt à {ug['ug_id']}."
                    )
                    break

                wkt_4326, wkt_3857 = geom_pairs[i]
                cur.execute(
                    update_query,
                    (wkt_4326, wkt_3857, file_name, str(ug["id"])),
                )
                print(f"    ✓ {ug['ug_id']} ({ug['libelle']})")

        conn.commit()
        print("  ✅ OK\n")
    except Exception:
        conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


def run_batch(
    *,
    shp_dir: Path,
    shuffle: bool,
    projet_filtre: UUID | None = None,
) -> tuple[int, int]:
    """Parcourt le mapping. Retourne (ok, erreurs)."""
    items = list(MAPPING_PROJETS_SHP.items())
    if projet_filtre is not None:
        key = str(projet_filtre)
        if key not in MAPPING_PROJETS_SHP:
            raise KeyError(
                f"Projet {key} absent du mapping "
                f"({len(MAPPING_PROJETS_SHP)} entrées)."
            )
        items = [(key, MAPPING_PROJETS_SHP[key])]

    ok = 0
    err = 0
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        for i, (projet_id_str, shp_name) in enumerate(items, start=1):
            shp_path = shp_dir / shp_name
            print(f"[{i}/{len(items)}] {projet_id_str} ← {shp_name}")
            try:
                assign_shp_to_project_ug(
                    UUID(projet_id_str),
                    shp_path,
                    shuffle=shuffle,
                    conn=conn,
                )
                ok += 1
            except Exception as exc:  # noqa: BLE001
                err += 1
                print(f"  ❌ {exc}\n", file=sys.stderr)

    return ok, err


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Assigne les SHP aux UG surfaciques selon MAPPING_PROJETS_SHP "
            "(batch une passe)."
        ),
    )
    parser.add_argument(
        "--shp-dir",
        type=Path,
        default=DEFAULT_SHP_DIR,
        help=f"Dossier des .shp (défaut : {DEFAULT_SHP_DIR})",
    )
    parser.add_argument(
        "--projet",
        type=UUID,
        default=None,
        help="Limiter à un seul projet du mapping (sinon tous)",
    )
    parser.add_argument(
        "--no-shuffle",
        action="store_true",
        help="Garder l'ordre des features du SHP",
    )
    args = parser.parse_args()

    shp_dir = args.shp_dir.expanduser().resolve()
    if not shp_dir.is_dir():
        print(f"❌ Dossier SHP introuvable : {shp_dir}", file=sys.stderr)
        return 1

    try:
        ok, err = run_batch(
            shp_dir=shp_dir,
            shuffle=not args.no_shuffle,
            projet_filtre=args.projet,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    print(f"Terminé : {ok} OK, {err} erreur(s).")
    return 1 if err else 0


if __name__ == "__main__":
    raise SystemExit(main())
