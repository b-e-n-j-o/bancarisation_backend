"""Routes d'export de projet. L'identité vient du JWT, les données de la RLS."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from psycopg.rows import dict_row

from auth.deps import get_claims
from api.db.utilisateur import connect_utilisateur
from api.export.archive import url_signee
from api.export.contexte import connect_backend
from api.export.service import executer_export
from api.export.sqlutil import lignes, une

router = APIRouter()


class OptionsExport(BaseModel):
    inclure_budget: bool = True
    inclure_documents: bool = True
    annee_min: int | None = Field(default=None, ge=1900, le=2200)
    annee_max: int | None = Field(default=None, ge=1900, le=2200)
    inclure_supprimees: bool = False


class EligibiliteOut(BaseModel):
    peut_exporter: bool
    taille_documents_estimee: int
    nb_documents: int
    annee_min: int | None
    annee_max: int | None


class ExportCreeOut(BaseModel):
    export_id: UUID


class TelechargementOut(BaseModel):
    url: str


def connect_utilisateur_claims(claims: dict[str, Any]):
    return connect_utilisateur(claims, row_factory=dict_row)


@router.get("/projets/{projet_id}/exports/eligibilite", response_model=EligibiliteOut)
def eligibilite(projet_id: UUID) -> EligibiliteOut:
    claims = get_claims()
    with connect_utilisateur_claims(claims) as conn:
        droit = une(
            conn,
            "select bancarisation.peut_exporter_projet(%s) as ok",
            (str(projet_id),),
        )
        docs = une(
            conn,
            """
            select count(*)::int as n, coalesce(sum(taille_octets), 0)::bigint as taille
              from bancarisation.documents
             where projet_id = %s
            """,
            (str(projet_id),),
        )
        annees = une(
            conn,
            """
            select min(annee) as annee_min, max(annee) as annee_max
              from bancarisation.occurrence
             where projet_id = %s
            """,
            (str(projet_id),),
        )
    return EligibiliteOut(
        peut_exporter=bool(droit and droit["ok"]),
        taille_documents_estimee=int((docs or {}).get("taille") or 0),
        nb_documents=int((docs or {}).get("n") or 0),
        annee_min=(annees or {}).get("annee_min"),
        annee_max=(annees or {}).get("annee_max"),
    )


@router.post(
    "/projets/{projet_id}/exports",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExportCreeOut,
)
def demander(projet_id: UUID, body: OptionsExport, taches: BackgroundTasks) -> ExportCreeOut:
    if body.annee_min is not None and body.annee_max is not None and body.annee_min > body.annee_max:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "La plage d'années est inversée.")
    claims = get_claims()
    user_id = str(claims["sub"])
    with connect_utilisateur_claims(claims) as conn:
        projet = une(
            conn,
            "select organisation_id::text from bancarisation.projets where id = %s",
            (str(projet_id),),
        )
        if not projet:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Projet introuvable.")
        droit = une(
            conn,
            "select bancarisation.peut_exporter_projet(%s) as ok",
            (str(projet_id),),
        )
        if not droit or not droit["ok"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Seul un admin de l'organisation propriétaire peut exporter.")
    try:
        with connect_utilisateur_claims(claims) as conn:
            cree = une(
                conn,
                """
                insert into bancarisation.export_projet
                  (projet_id, organisation_id, demande_par, options)
                values (%s, %s, %s, %s)
                returning id::text
                """,
                (
                    str(projet_id),
                    projet["organisation_id"],
                    user_id,
                    Jsonb(body.model_dump()),
                ),
            )
    except UniqueViolation as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Un export est déjà en cours pour ce projet.",
        ) from exc
    if not cree:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Export refusé.")
    taches.add_task(executer_export, cree["id"], user_id, claims.get("email"))
    return ExportCreeOut(export_id=UUID(cree["id"]))


@router.get("/projets/{projet_id}/exports")
def historique(projet_id: UUID) -> dict[str, list[dict[str, Any]]]:
    claims = get_claims()
    with connect_utilisateur_claims(claims) as conn:
        rows = lignes(
            conn,
            """
            select id::text, projet_id::text, demande_par::text, statut, progression,
                   taille_octets, sha256, erreur, created_at, termine_le, expire_le,
                   nb_telechargements, manifest
              from bancarisation.export_projet
             where projet_id = %s
             order by created_at desc
            """,
            (str(projet_id),),
        )
    return {"exports": [_public(r) for r in rows]}


@router.get("/exports/{export_id}")
def statut_export(export_id: UUID) -> dict[str, Any]:
    claims = get_claims()
    with connect_utilisateur_claims(claims) as conn:
        row = une(
            conn,
            """
            select id::text, projet_id::text, demande_par::text, statut, progression,
                   taille_octets, sha256, erreur, created_at, termine_le, expire_le,
                   nb_telechargements, manifest, options
              from bancarisation.export_projet
             where id = %s
            """,
            (str(export_id),),
        )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export introuvable.")
    return _public(row)


@router.post("/exports/{export_id}/telechargement", response_model=TelechargementOut)
def telechargement(export_id: UUID) -> TelechargementOut:
    claims = get_claims()
    with connect_utilisateur_claims(claims) as conn:
        row = une(
            conn,
            """
            select id::text, statut, storage_path, expire_le, projet_id::text
              from bancarisation.export_projet
             where id = %s
            """,
            (str(export_id),),
        )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export introuvable.")
    if row["statut"] not in ("termine", "termine_avec_avertissements"):
        raise HTTPException(status.HTTP_409_CONFLICT, "L'export n'est pas téléchargeable.")
    expire = row.get("expire_le")
    if expire is not None:
        from datetime import datetime, timezone

        moment = expire if getattr(expire, "tzinfo", None) else expire.replace(tzinfo=timezone.utc)
        if moment < datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_410_GONE, "Cet export a expiré.")
    if not row.get("storage_path"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Fichier d'export absent.")
    with connect_backend() as conn:
        conn.execute(
            """
            update bancarisation.export_projet
               set nb_telechargements = nb_telechargements + 1,
                   dernier_telechargement_le = now()
             where id = %s
            """,
            (str(export_id),),
        )
    nom = f"export-{row['projet_id'][:8]}.zip"
    return TelechargementOut(url=url_signee(row["storage_path"], nom))


def _public(row: dict[str, Any]) -> dict[str, Any]:
    manifest = row.get("manifest") or {}
    avertissements = manifest.get("avertissements") if isinstance(manifest, dict) else []
    genere = (manifest or {}).get("genere_par") if isinstance(manifest, dict) else {}
    return {
        "id": row["id"],
        "projet_id": row.get("projet_id"),
        "demande_par": row.get("demande_par"),
        "demandeur_email": (genere or {}).get("email") if isinstance(genere, dict) else None,
        "statut": row.get("statut"),
        "progression": row.get("progression") or {},
        "taille_octets": row.get("taille_octets"),
        "sha256": row.get("sha256"),
        "erreur": row.get("erreur"),
        "created_at": row.get("created_at"),
        "termine_le": row.get("termine_le"),
        "expire_le": row.get("expire_le"),
        "nb_telechargements": row.get("nb_telechargements") or 0,
        "avertissements": avertissements or [],
        "options": row.get("options"),
    }
