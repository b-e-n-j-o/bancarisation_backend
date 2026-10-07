"""Actions utilisateur sur les entités géométriques (origine=user, journalisées)."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

import psycopg

from api.db.utilisateur import connect_utilisateur
from api.ocr.domain.ug_ids import normalize_ug_id

from .ingestion import GeometryIngestError

TypeGeom = Literal["surf", "lin", "pct"]
StatutEntite = Literal["ug", "contexte", "non_affectee", "ecartee"]

_TABLES: dict[str, str] = {
    "surf": "unites_de_gestion_surf",
    "lin": "unites_de_gestion_lin",
    "pct": "unites_de_gestion_pct",
}

_STATUTS = {"ug", "contexte", "non_affectee", "ecartee"}


def _table(kind: str) -> str:
    if kind not in _TABLES:
        raise GeometryIngestError(f"type géométrique inconnu : {kind}")
    return _TABLES[kind]


def _libelle_ug(cur: Any, projet_id: str, ug_id: str) -> str:
    for table in _TABLES.values():
        cur.execute(
            f"""
            SELECT libelle FROM bancarisation.{table}
            WHERE projet_id = %s AND ug_id = %s AND statut = 'ug'
              AND libelle IS NOT NULL AND trim(libelle) <> ''
            LIMIT 1
            """,
            (projet_id, ug_id),
        )
        row = cur.fetchone()
        if row and row[0]:
            return str(row[0])
    return ug_id


def _journaliser(
    cur: Any,
    *,
    projet_id: str,
    table_source: str,
    entite_id: str,
    ancien_statut: str | None,
    nouveau_statut: str | None,
    ancien_ug_id: str | None,
    nouveau_ug_id: str | None,
    motif: str | None,
    auteur: str | None,
) -> None:
    cur.execute(
        """
        INSERT INTO bancarisation.geometrie_mouvement
            (projet_id, table_source, entite_id,
             ancien_statut, nouveau_statut, ancien_ug_id, nouveau_ug_id,
             motif, auteur)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            projet_id, table_source, entite_id,
            ancien_statut, nouveau_statut, ancien_ug_id, nouveau_ug_id,
            motif, auteur,
        ),
    )


def _charger(cur: Any, kind: str, entite_id: str) -> dict[str, Any]:
    table = _table(kind)
    cur.execute(
        f"""
        SELECT id::text, projet_id::text, ug_id, libelle, statut, couche
        FROM bancarisation.{table}
        WHERE id = %s
        """,
        (entite_id,),
    )
    row = cur.fetchone()
    if not row:
        raise GeometryIngestError(f"Entité introuvable : {kind}/{entite_id}")
    cols = [d.name for d in cur.description]
    return dict(zip(cols, row))


def _appliquer(
    cur: Any,
    *,
    kind: str,
    entite_id: str,
    statut: StatutEntite,
    ug_id: str | None,
    libelle: str | None,
    motif: str | None,
    auteur: str | None,
) -> dict[str, Any]:
    row = _charger(cur, kind, entite_id)
    if statut == "ug":
        if not ug_id:
            raise GeometryIngestError("ug_id obligatoire lorsque statut='ug'.")
    else:
        ug_id = None

    table = _table(kind)
    cur.execute(
        f"""
        UPDATE bancarisation.{table}
        SET statut = %s,
            ug_id = %s,
            libelle = COALESCE(%s, libelle),
            motif = COALESCE(%s, motif),
            origine = 'user',
            modifie_le = now(),
            updated_at = now()
        WHERE id = %s
        RETURNING id::text, projet_id::text, ug_id, libelle, statut, couche, motif
        """,
        (statut, ug_id, libelle, motif, entite_id),
    )
    updated = cur.fetchone()
    cols = [d.name for d in cur.description]
    out = dict(zip(cols, updated))
    _journaliser(
        cur,
        projet_id=row["projet_id"],
        table_source=kind,
        entite_id=entite_id,
        ancien_statut=row.get("statut"),
        nouveau_statut=statut,
        ancien_ug_id=row.get("ug_id"),
        nouveau_ug_id=ug_id,
        motif=motif,
        auteur=auteur,
    )
    out["type"] = kind
    return out


def patch_entite(
    kind: str,
    entite_id: UUID | str,
    *,
    statut: str,
    ug_id: str | None = None,
    motif: str | None = None,
    auteur: str | None = None,
) -> dict[str, Any]:
    if statut not in _STATUTS:
        raise GeometryIngestError(f"statut inconnu : {statut}")
    code = normalize_ug_id(ug_id) if ug_id else None
    if statut == "ug" and not code:
        raise GeometryIngestError("ug_id invalide.")
    if statut != "ug":
        code = None

    try:
        with connect_utilisateur() as conn:
            with conn.cursor() as cur:
                row = _charger(cur, kind, str(entite_id))
                libelle = _libelle_ug(cur, row["projet_id"], code) if code else None
                out = _appliquer(
                    cur,
                    kind=kind,
                    entite_id=str(entite_id),
                    statut=statut,  # type: ignore[arg-type]
                    ug_id=code,
                    libelle=libelle,
                    motif=motif,
                    auteur=auteur,
                )
            conn.commit()
    except GeometryIngestError:
        raise
    except Exception as exc:
        raise GeometryIngestError(f"Mise à jour impossible : {exc}") from exc
    return out


def _entites_payload(entites: list[dict[str, Any]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for e in entites:
        kind = str(e.get("type") or "")
        eid = str(e.get("id") or "")
        if kind not in _TABLES or not eid:
            raise GeometryIngestError(f"entité invalide : {e}")
        out.append((kind, eid))
    if not out:
        raise GeometryIngestError("Aucune entité.")
    return out


def affecter_entites(
    entites: list[dict[str, Any]],
    ug_id: str,
    *,
    motif: str | None = None,
    auteur: str | None = None,
) -> dict[str, Any]:
    code = normalize_ug_id(ug_id)
    if not code:
        raise GeometryIngestError("ug_id invalide.")
    pairs = _entites_payload(entites)
    updated: list[dict[str, Any]] = []
    try:
        with connect_utilisateur() as conn:
            with conn.cursor() as cur:
                first = _charger(cur, pairs[0][0], pairs[0][1])
                libelle = _libelle_ug(cur, first["projet_id"], code)
                for kind, eid in pairs:
                    updated.append(
                        _appliquer(
                            cur,
                            kind=kind,
                            entite_id=eid,
                            statut="ug",
                            ug_id=code,
                            libelle=libelle,
                            motif=motif or f"affecté à {code}",
                            auteur=auteur,
                        )
                    )
            conn.commit()
    except GeometryIngestError:
        raise
    except Exception as exc:
        raise GeometryIngestError(f"Affectation impossible : {exc}") from exc
    return {"ug_id": code, "libelle": libelle, "entites": updated, "nb": len(updated)}


def creer_ug_depuis_geometries(
    projet_id: UUID | str,
    *,
    entites: list[dict[str, Any]],
    code: str,
    libelle: str,
    type_erc: str | None = None,
    auteur: str | None = None,
) -> dict[str, Any]:
    ug = normalize_ug_id(code)
    if not ug:
        raise GeometryIngestError("code UG invalide.")
    nom = (libelle or "").strip() or ug
    pairs = _entites_payload(entites)
    motif = f"UG {ug} créée depuis une sélection"
    updated: list[dict[str, Any]] = []
    try:
        with connect_utilisateur() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM bancarisation.projets WHERE id = %s",
                    (str(projet_id),),
                )
                if not cur.fetchone():
                    raise GeometryIngestError("Projet introuvable.")
                for kind, eid in pairs:
                    row = _charger(cur, kind, eid)
                    if row["projet_id"] != str(projet_id):
                        raise GeometryIngestError(
                            f"Entité {kind}/{eid} hors projet."
                        )
                    out = _appliquer(
                        cur,
                        kind=kind,
                        entite_id=eid,
                        statut="ug",
                        ug_id=ug,
                        libelle=nom,
                        motif=motif,
                        auteur=auteur,
                    )
                    if type_erc:
                        table = _table(kind)
                        cur.execute(
                            f"""
                            UPDATE bancarisation.{table}
                            SET categorie_erc = %s, description = COALESCE(NULLIF(description, ''), %s)
                            WHERE id = %s
                            """,
                            (type_erc, type_erc, eid),
                        )
                    updated.append(out)
            conn.commit()
    except GeometryIngestError:
        raise
    except Exception as exc:
        raise GeometryIngestError(f"Création UG impossible : {exc}") from exc
    return {
        "ug_id": ug,
        "libelle": nom,
        "type_erc": type_erc,
        "entites": updated,
        "nb": len(updated),
    }


def retirer_entites(
    entites: list[dict[str, Any]],
    *,
    motif: str | None = None,
    auteur: str | None = None,
) -> dict[str, Any]:
    pairs = _entites_payload(entites)
    updated: list[dict[str, Any]] = []
    try:
        with connect_utilisateur() as conn:
            with conn.cursor() as cur:
                for kind, eid in pairs:
                    updated.append(
                        _appliquer(
                            cur,
                            kind=kind,
                            entite_id=eid,
                            statut="non_affectee",
                            ug_id=None,
                            libelle=None,
                            motif=motif or "retiré d'une UG",
                            auteur=auteur,
                        )
                    )
            conn.commit()
    except GeometryIngestError:
        raise
    except Exception as exc:
        raise GeometryIngestError(f"Retrait impossible : {exc}") from exc
    return {"entites": updated, "nb": len(updated)}
