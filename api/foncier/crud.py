"""Lecture GeoJSON des parcelles foncières persistées à l'ingestion."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg

from api.db.utilisateur import connect_utilisateur

from api.projets.geometries.foncier_croisement import (
    FoncierCroisementError,
    persister_parcelles_concernees,
)


class FoncierError(Exception):
    pass


def _connect():
    return connect_utilisateur()


def _lire_features(projet_id: UUID) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  p.id::text,
                  p.idu,
                  p.commune,
                  p.section,
                  p.numero,
                  p.contenance_m2,
                  ST_AsGeoJSON(p.geom_4326)::text
                FROM bancarisation.parcelles p
                WHERE p.projet_id = %s
                  AND p.geom_4326 IS NOT NULL
                ORDER BY p.section NULLS LAST, p.numero NULLS LAST, p.idu
                """,
                (str(projet_id),),
            )
            by_id: dict[str, dict[str, Any]] = {}
            for pid, idu, commune, section, numero, contenance, geom_json in cur.fetchall():
                if not geom_json:
                    continue
                label = f"{section or ''} {numero or ''}".strip() or (idu or "")
                feat = {
                    "type": "Feature",
                    "id": pid,
                    "geometry": json.loads(geom_json),
                    "properties": {
                        "id": pid,
                        "idu": idu,
                        "commune": commune,
                        "nom_com": commune,
                        "section": section,
                        "numero": numero,
                        "contenance": float(contenance) if contenance is not None else None,
                        "contenance_m2": float(contenance) if contenance is not None else None,
                        "label": label,
                        "ugs": [],
                    },
                }
                by_id[pid] = feat
                features.append(feat)

            if not by_id:
                return features

            cur.execute(
                """
                SELECT p.id::text, COALESCE(NULLIF(u.libelle, ''), u.ug_id)
                FROM bancarisation.parcelles p
                JOIN bancarisation.ug_surf_parcelles l ON l.parcelle_id = p.id
                JOIN bancarisation.unites_de_gestion_surf u ON u.id = l.ug_id
                WHERE p.projet_id = %s
                UNION
                SELECT p.id::text, COALESCE(NULLIF(u.libelle, ''), u.ug_id)
                FROM bancarisation.parcelles p
                JOIN bancarisation.ug_lin_parcelles l ON l.parcelle_id = p.id
                JOIN bancarisation.unites_de_gestion_lin u ON u.id = l.ug_id
                WHERE p.projet_id = %s
                UNION
                SELECT p.id::text, COALESCE(NULLIF(u.libelle, ''), u.ug_id)
                FROM bancarisation.parcelles p
                JOIN bancarisation.ug_pct_parcelles l ON l.parcelle_id = p.id
                JOIN bancarisation.unites_de_gestion_pct u ON u.id = l.ug_id
                WHERE p.projet_id = %s
                """,
                (str(projet_id), str(projet_id), str(projet_id)),
            )
            for pid, label in cur.fetchall():
                feat = by_id.get(pid)
                if not feat:
                    continue
                ugs = feat["properties"].setdefault("ugs", [])
                if label and label not in ugs:
                    ugs.append(label)
    return features


def geojson_parcelles(projet_id: UUID) -> dict[str, Any]:
    """FeatureCollection 4326 des parcelles concernées persistées à l'ingestion."""
    try:
        features = _lire_features(projet_id)
        if not features:
            # Ancien projet : snapshot IGN déjà là, copie manquante vers parcelles.
            persister_parcelles_concernees(projet_id)
            features = _lire_features(projet_id)
    except FoncierCroisementError as exc:
        raise FoncierError(str(exc)) from exc
    except Exception as exc:
        raise FoncierError(f"Lecture geojson foncier impossible: {exc}") from exc

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {"nb_parcelles": len(features)},
    }
