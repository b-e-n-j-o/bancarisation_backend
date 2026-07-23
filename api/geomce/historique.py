"""Lecture / écriture historique export_geomce + chargement projet."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.db.env import get_database_url
from api.projets.geometries.crud import lister_geometries_ug


STORAGE_ROOT = Path(__file__).resolve().parents[2] / "storage" / "geomce"


def _conn():
    return psycopg.connect(get_database_url(), row_factory=dict_row)


def lire_projet(projet_id: UUID | str) -> dict[str, Any] | None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  id, nom, commune, departement, description, statut,
                  date_decision, duree_annees, date_fin, reference_interne,
                  geomce_nom, geomce_nom_verrouille, geomce_categorie,
                  geomce_cible, geomce_description,
                  geomce_projet_libelle, geomce_procedure_libelle,
                  reference_decision, reference_ei
                FROM bancarisation.projets
                WHERE id = %s
                """,
                (str(projet_id),),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def mettre_a_jour_champs_geomce(projet_id: UUID | str, patch: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "geomce_nom",
        "geomce_categorie",
        "geomce_cible",
        "geomce_description",
        "geomce_projet_libelle",
        "geomce_procedure_libelle",
        "reference_decision",
        "reference_ei",
    }
    updates = {k: v for k, v in patch.items() if k in allowed}
    if not updates:
        p = lire_projet(projet_id)
        if not p:
            raise ValueError("Projet introuvable")
        return p

    # Ne pas écraser un nom verrouillé
    projet = lire_projet(projet_id)
    if not projet:
        raise ValueError("Projet introuvable")
    if projet.get("geomce_nom_verrouille") and "geomce_nom" in updates:
        if updates["geomce_nom"] != projet.get("geomce_nom"):
            raise ValueError("Le nom GéoMCE est verrouillé après le premier versement.")

    sets = []
    args: list[Any] = []
    for k, v in updates.items():
        sets.append(f"{k} = %s")
        if k == "geomce_cible" and isinstance(v, list):
            args.append(v)
        else:
            args.append(v)
    args.append(str(projet_id))

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE bancarisation.projets
                SET {', '.join(sets)}, updated_at = now()
                WHERE id = %s
                RETURNING id
                """,
                args,
            )
            conn.commit()
    out = lire_projet(projet_id)
    assert out
    return out


def lister_categories() -> list[dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT code, libelle, niveau, parent
                FROM bancarisation.ref_geomce_categorie
                ORDER BY niveau, code
                """
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def categories_codes() -> set[str]:
    return {r["code"] for r in lister_categories()}


def features_ug(projet_id: UUID | str) -> list[dict[str, Any]]:
    fc = lister_geometries_ug(UUID(str(projet_id)))
    return list(fc.get("features") or [])


def lister_historique(projet_id: UUID | str) -> list[dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  id, projet_id, cree_le, cree_par, mode, strategie_geom,
                  srid, nb_polygones, surface_ha, geom_hash, attributs,
                  nom_fichier, storage_path, statut, verse_le, commentaire
                FROM bancarisation.export_geomce
                WHERE projet_id = %s
                ORDER BY cree_le DESC
                """,
                (str(projet_id),),
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("attributs") and isinstance(d["attributs"], str):
            d["attributs"] = json.loads(d["attributs"])
        if d.get("cree_le"):
            d["cree_le"] = d["cree_le"].isoformat()
        if d.get("verse_le"):
            d["verse_le"] = d["verse_le"].isoformat()
        if d.get("surface_ha") is not None:
            d["surface_ha"] = float(d["surface_ha"])
        d["id"] = str(d["id"])
        d["projet_id"] = str(d["projet_id"])
        out.append(d)
    return out


def dernier_verse(projet_id: UUID | str) -> dict[str, Any] | None:
    hist = lister_historique(projet_id)
    for h in hist:
        if h.get("statut") == "verse":
            return h
    return None


def inserer_export(
    *,
    projet_id: UUID | str,
    mode: str,
    strategie_geom: str,
    srid: int,
    nb_polygones: int,
    surface_ha: float | None,
    geom_hash: str,
    attributs: dict[str, Any],
    nom_fichier: str,
    storage_path: str,
    cree_par: str | None = None,
) -> dict[str, Any]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bancarisation.export_geomce (
                  projet_id, cree_par, mode, strategie_geom, srid,
                  nb_polygones, surface_ha, geom_hash, attributs,
                  nom_fichier, storage_path, statut
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, 'genere'
                )
                RETURNING id, cree_le, statut, nom_fichier, storage_path
                """,
                (
                    str(projet_id),
                    cree_par,
                    mode,
                    strategie_geom,
                    srid,
                    nb_polygones,
                    surface_ha,
                    geom_hash,
                    Jsonb(attributs),
                    nom_fichier,
                    storage_path,
                ),
            )
            row = cur.fetchone()
            conn.commit()
    d = dict(row)
    d["id"] = str(d["id"])
    d["cree_le"] = d["cree_le"].isoformat()
    return d


def patch_export(
    export_id: UUID | str,
    *,
    statut: str | None = None,
    verse_le: date | None = None,
    commentaire: str | None = None,
    verrouiller_nom_projet_id: str | None = None,
) -> dict[str, Any]:
    sets: list[str] = []
    args: list[Any] = []
    if statut is not None:
        sets.append("statut = %s")
        args.append(statut)
    if verse_le is not None:
        sets.append("verse_le = %s")
        args.append(verse_le)
    if commentaire is not None:
        sets.append("commentaire = %s")
        args.append(commentaire)
    if not sets:
        raise ValueError("Rien à mettre à jour")
    args.append(str(export_id))

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE bancarisation.export_geomce
                SET {', '.join(sets)}
                WHERE id = %s
                RETURNING id, projet_id, statut, verse_le, commentaire, nom_fichier, storage_path
                """,
                args,
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Export introuvable")

            if statut == "verse" and verrouiller_nom_projet_id:
                cur.execute(
                    """
                    UPDATE bancarisation.projets
                    SET geomce_nom_verrouille = true, updated_at = now()
                    WHERE id = %s
                    """,
                    (verrouiller_nom_projet_id,),
                )
            conn.commit()
    d = dict(row)
    d["id"] = str(d["id"])
    d["projet_id"] = str(d["projet_id"])
    if d.get("verse_le"):
        d["verse_le"] = d["verse_le"].isoformat()
    return d


def get_export(export_id: UUID | str) -> dict[str, Any] | None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, projet_id, nom_fichier, storage_path, statut
                FROM bancarisation.export_geomce WHERE id = %s
                """,
                (str(export_id),),
            )
            row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    d["projet_id"] = str(d["projet_id"])
    return d
