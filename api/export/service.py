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
    debut = datetime.now(timezone.utc)
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
        _verifier_espace(ctx)
        for module in MODULES:
            avant = len(ctx.avertissements)
            try:
                with connect_export(ctx) as conn:
                    produits = module.produire(ctx, conn)
                for fic in produits:
                    ancien = fic.chemin_archive
                    fic.chemin_archive = reserver(fic.chemin_archive, ctx.racine_zip, ctx.chemins_pris)
                    if fic.chemin_archive != ancien:
                        for cle, chemin in list(ctx.alias_fichiers.items()):
                            if chemin == ancien:
                                ctx.alias_fichiers[cle] = fic.chemin_archive
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

        from datetime import datetime as _dt

        from api.export.fichiers_index import ecrire_index_fichiers
        from api.export.pdf.index_html import ecrire_index, ecrire_lisezmoi

        index = ecrire_index_fichiers(ctx, fichiers)
        if index is not None:
            index.chemin_archive = reserver(index.chemin_archive, ctx.racine_zip, ctx.chemins_pris)
            fichiers.append(index)

        genere_le = _dt.now().strftime("%d/%m/%Y")
        try:
            fichiers.append(ecrire_lisezmoi(
                ctx,
                fichiers,
                projet_nom=projet.get("nom") or "Projet",
                organisation_nom=projet.get("organisation_nom") or "",
                genere_le=genere_le,
                demandeur=email,
            ))
        except Exception as exc:
            ctx.avertir(f"LISEZMOI : {exc}")
        try:
            fichiers.append(ecrire_index(
                ctx,
                fichiers,
                projet_nom=projet.get("nom") or "Projet",
                organisation_nom=projet.get("organisation_nom") or "",
                genere_le=genere_le,
            ))
        except Exception as exc:
            ctx.avertir(f"index.html : {exc}")

        manifest = construire(
            ctx,
            fichiers,
            modules_meta,
            email=email,
            organisation_nom=projet.get("organisation_nom") or "",
            projet_nom=projet.get("nom") or "",
            reference=projet.get("reference_interne"),
        )
        maintenant = datetime.now(timezone.utc)
        manifest["duree_secondes"] = round((maintenant - debut).total_seconds(), 1)
        chemin_manifest = ecrire(dossier, manifest)
        zip_path = ecrire_zip(dossier, ctx.racine_zip, fichiers, chemin_manifest)
        sha, taille = empreinte_fichier(zip_path)
        storage_path = f"{ctx.organisation_id}/{ctx.projet_id}/{export_id}.zip"
        uploader(zip_path, storage_path)
        statut = "termine_avec_avertissements" if ctx.avertissements else "termine"
        jours = ctx.options.conservation_jours
        expire = maintenant + timedelta(days=jours)
        _marquer(
            str(export_id),
            statut=statut,
            storage_path=storage_path,
            taille_octets=taille,
            sha256=sha,
            manifest=manifest,
            termine_le=maintenant,
            expire_le=expire,
            progression={"etape": "termine", "modules_faits": faits, "total": len(MODULES)},
        )
        lien = f"{ctx.url_front}/projets/{ctx.projet_id}?export={export_id}"
        try:
            if email:
                envoyer_export_pret(
                    email,
                    projet.get("nom") or "projet",
                    lien,
                    taille_octets=taille,
                    expire_le=expire,
                    nb_avertissements=len(ctx.avertissements),
                    conservation_jours=jours,
                )
        except Exception:
            logger.exception("Mail d'export non envoyé")
    finally:
        shutil.rmtree(dossier, ignore_errors=True)


def _verifier_espace(ctx: ExportContexte) -> None:
    estime = 100 * 1024 * 1024
    if ctx.options.inclure_documents:
        try:
            with connect_export(ctx) as conn:
                row = une(
                    conn,
                    """
                    select coalesce(sum(taille_octets), 0)::bigint as taille
                      from bancarisation.documents
                     where projet_id = %s
                    """,
                    (str(ctx.projet_id),),
                )
            estime += int((row or {}).get("taille") or 0) * 1.2
        except Exception:
            logger.exception("Estimation de taille impossible")
    libre = shutil.disk_usage(ctx.dossier_tmp).free
    if libre < estime:
        mo = lambda n: max(1, int(n / (1024 * 1024)))
        raise RuntimeError(
            f"Espace disque insuffisant pour l'export : il faut environ {mo(estime)} Mo, "
            f"il reste {mo(libre)} Mo."
        )


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
