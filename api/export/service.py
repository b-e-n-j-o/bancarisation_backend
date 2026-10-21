"""Orchestration du job d'export. Les lectures passent par la RLS du demandeur."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from api.export.archive import empreinte_fichier, ecrire_zip, uploader
from api.export.contexte import ExportContexte, ExportOptions, FichierExport, connect_backend, connect_export
from api.export.mailer import envoyer_export_pret
from api.export.manifest import construire, ecrire
from api.export.modules import MODULES
from api.export.nommage import racine_zip, reserver
from api.export.sqlutil import une

logger = logging.getLogger("api.export.service")


def url_front() -> str:
    return os.environ.get("URL_FRONT", "https://bancarisation.kerelia.fr").rstrip("/")


def marquer_exports_interrompus() -> None:
    """Au démarrage : un export encore « en cours » depuis plus d'une heure a été coupé."""
    try:
        with connect_backend() as conn:
            conn.execute(
                """
                update bancarisation.export_projet
                   set statut = 'echec',
                       erreur = 'interrompu par un redémarrage',
                       termine_le = now()
                 where statut = 'en_cours'
                   and created_at < now() - interval '1 hour'
                """
            )
    except Exception:
        logger.exception("Impossible de clôturer les exports interrompus")


def executer_export(export_id: str, user_id: str, email: str | None) -> None:
    try:
        _executer(UUID(export_id), UUID(user_id), email)
    except Exception as exc:
        logger.exception("Export %s en échec", export_id)
        _marquer(
            export_id,
            statut="echec",
            erreur=str(exc)[:800],
            termine_le=datetime.now(timezone.utc),
        )


def _executer(export_id: UUID, user_id: UUID, email: str | None) -> None:
    with connect_backend() as conn:
        ligne = une(
            conn,
            "select * from bancarisation.export_projet where id = %s",
            (str(export_id),),
        )
    if not ligne:
        raise RuntimeError("Ligne d'export introuvable.")
    _marquer(str(export_id), statut="en_cours", progression={"etape": "demarrage", "modules_faits": [], "total": len(MODULES)})

    options = ExportOptions.depuis_json(ligne.get("options"))
    dossier = Path(tempfile.mkdtemp(prefix="kererc-export-"))
    try:
        ctx = ExportContexte(
            export_id=export_id,
            projet_id=UUID(str(ligne["projet_id"])),
            user_id=user_id,
            organisation_id=UUID(str(ligne["organisation_id"])),
            options=options,
            dossier_tmp=dossier,
            url_front=url_front(),
        )
        with connect_export(ctx) as conn:
            projet = une(
                conn,
                """
                select p.nom, p.reference_interne, o.nom as organisation_nom
                  from bancarisation.projets p
                  join bancarisation.organisations o on o.id = p.organisation_id
                 where p.id = %s
                """,
                (str(ctx.projet_id),),
            )
        if not projet:
            raise RuntimeError("Projet invisible pour le demandeur.")
        jour = datetime.now().strftime("%Y-%m-%d")
        ctx.racine_zip = racine_zip(
            projet.get("reference_interne"),
            str(ctx.projet_id),
            projet.get("nom") or "projet",
            jour,
        )

        fichiers: list[FichierExport] = []
        modules_meta: dict[str, dict[str, Any]] = {}
        faits: list[str] = []
        for module in MODULES:
            avant = len(ctx.avertissements)
            try:
                with connect_export(ctx) as conn:
                    produits = module.produire(ctx, conn)
                for fic in produits:
                    fic.chemin_archive = reserver(fic.chemin_archive, ctx.racine_zip, ctx.chemins_pris)
                fichiers.extend(produits)
                messages = ctx.avertissements[avant:]
                modules_meta[module.nom] = {
                    "statut": "avertissement" if messages else "ok",
                    "messages": messages,
                }
            except Exception as exc:
                logger.exception("Module %s", module.nom)
                message = f"{module.nom} : {exc}"
                ctx.avertir(message[:500])
                modules_meta[module.nom] = {
                    "statut": "avertissement",
                    "messages": [str(exc)[:500]],
                }
            faits.append(module.nom)
            _marquer(
                str(export_id),
                progression={
                    "etape": module.nom,
                    "modules_faits": list(faits),
                    "total": len(MODULES),
                },
            )

        manifest = construire(
            ctx,
            fichiers,
            modules_meta,
            email=email,
            organisation_nom=projet.get("organisation_nom") or "",
            projet_nom=projet.get("nom") or "",
            reference=projet.get("reference_interne"),
        )
        chemin_manifest = ecrire(dossier, manifest)
        zip_path = ecrire_zip(dossier, ctx.racine_zip, fichiers, chemin_manifest)
        sha, taille = empreinte_fichier(zip_path)
        storage_path = f"{ctx.organisation_id}/{ctx.projet_id}/{export_id}.zip"
        uploader(zip_path, storage_path)
        statut = "termine_avec_avertissements" if ctx.avertissements else "termine"
        maintenant = datetime.now(timezone.utc)
        _marquer(
            str(export_id),
            statut=statut,
            storage_path=storage_path,
            taille_octets=taille,
            sha256=sha,
            manifest=manifest,
            termine_le=maintenant,
            expire_le=maintenant + timedelta(days=7),
            progression={"etape": "termine", "modules_faits": faits, "total": len(MODULES)},
        )
        lien = f"{ctx.url_front}/projets/{ctx.projet_id}?export={export_id}"
        try:
            if email:
                envoyer_export_pret(
                    email,
                    projet.get("nom") or "projet",
                    lien,
                    bool(ctx.avertissements),
                )
        except Exception:
            logger.exception("Mail d'export non envoyé")
    finally:
        shutil.rmtree(dossier, ignore_errors=True)


def _marquer(export_id: str, **champs: Any) -> None:
    if not champs:
        return
    sets = []
    valeurs: list[Any] = []
    for cle, valeur in champs.items():
        sets.append(f"{cle} = %s")
        if cle in {"progression", "manifest"} and not isinstance(valeur, Jsonb):
            valeurs.append(Jsonb(valeur))
        else:
            valeurs.append(valeur)
    valeurs.append(export_id)
    with connect_backend() as conn:
        conn.execute(
            f"update bancarisation.export_projet set {', '.join(sets)} where id = %s",
            valeurs,
        )
