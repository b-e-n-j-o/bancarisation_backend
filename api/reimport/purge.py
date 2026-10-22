"""Retire les ZIP d'import du bucket privé.

Les dépôts contiennent le dossier complet du client. On les efface dès que
l'import est terminé ou en échec, et au bout de 7 jours s'il n'a pas été confirmé.

    python -m api.reimport.purge
"""

from __future__ import annotations

import logging

from api.db.supabase import get_supabase_admin
from api.export.contexte import connect_backend
from api.export.sqlutil import lignes
from api.reimport.chargeur import marquer

logger = logging.getLogger("api.reimport.purge")
BUCKET = "imports-projet"


def oublier_zip(import_id: str, storage_path: str | None) -> None:
    if not storage_path:
        return
    try:
        get_supabase_admin().storage.from_(BUCKET).remove([storage_path])
    except Exception:
        logger.exception("Suppression du ZIP d'import impossible pour %s", import_id)
        return
    marquer(import_id, storage_path=None)


def purger() -> int:
    with connect_backend() as conn:
        dus = lignes(
            conn,
            """
            select id::text, storage_path
              from bancarisation.import_projet
             where storage_path is not null
               and (
                    statut in ('termine', 'echec')
                 or (statut in ('televerse', 'analyse', 'pret')
                     and created_at < now() - interval '7 days')
               )
            """,
        )
    n = 0
    for ligne in dus:
        oublier_zip(ligne["id"], ligne.get("storage_path"))
        n += 1
    logger.info("%s archive(s) d'import retirée(s)", n)
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    purger()


if __name__ == "__main__":
    main()
