"""Expire les ZIP dont la date est passée. La ligne de journal est conservée.

    python -m api.export.purge

À planifier une fois par jour (tâche Coolify).
"""

from __future__ import annotations

import logging

from api.export.archive import supprimer_objet
from api.export.contexte import connect_backend
from api.export.sqlutil import lignes

logger = logging.getLogger("api.export.purge")


def purger() -> int:
    with connect_backend() as conn:
        echeances = lignes(
            conn,
            """
            select id::text, storage_path
              from bancarisation.export_projet
             where expire_le is not null
               and expire_le < now()
               and statut in ('termine', 'termine_avec_avertissements')
            """,
        )
    n = 0
    for ligne in echeances:
        chemin = ligne.get("storage_path")
        if chemin:
            try:
                supprimer_objet(chemin)
            except Exception:
                logger.exception("Suppression storage impossible pour %s", ligne["id"])
                continue
        with connect_backend() as conn:
            conn.execute(
                """
                update bancarisation.export_projet
                   set statut = 'expire'
                 where id = %s
                """,
                (ligne["id"],),
            )
        n += 1
    logger.info("%s export(s) expiré(s)", n)
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    purger()


if __name__ == "__main__":
    main()
