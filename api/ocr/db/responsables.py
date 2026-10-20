"""Responsables d'une occurrence : plusieurs personnes, liées aux contacts du projet."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import psycopg

_SQL_LISTE = """
select
  r.occurrence_id::text as occurrence_id,
  r.personne_id::text as personne_id,
  r.ordre,
  coalesce(nullif(trim(concat_ws(' ', p.prenom, p.nom)), ''), p.nom) as nom,
  p.fonction,
  role.role_libelle,
  role.role_categorie
from bancarisation.occurrence_responsable r
join bancarisation.personnes p on p.id = r.personne_id
left join lateral (
  select ref.libelle as role_libelle, ref.categorie as role_categorie
  from bancarisation.projet_contact pc
  join bancarisation.role_contact_ref ref on ref.code = pc.role_code
  where pc.projet_id = r.projet_id
    and pc.personne_id = r.personne_id
  order by ref.ordre
  limit 1
) role on true
where r.occurrence_id = any(%s::uuid[])
order by r.occurrence_id, r.ordre, nom
"""


def _ids(personne_ids: list[str]) -> list[str]:
    vus: list[str] = []
    for raw in personne_ids:
        pid = str(raw).strip()
        if pid and pid not in vus:
            vus.append(pid)
    return vus


def remplacer_responsables(
    cur: psycopg.Cursor,
    occurrence_id: str,
    personne_ids: list[str],
) -> None:
    """Remplace la liste. Le premier est recopié dans occurrence.responsable_id.

    Chaque personne absente des contacts du projet y est ajoutée :
    interlocuteur prestataire si elle est rattachée à un prestataire,
    sinon opérateur de compensation.
    """
    ids = _ids(personne_ids)
    if ids:
        cur.execute(
            """
            delete from bancarisation.occurrence_responsable
            where occurrence_id = %s
              and not (personne_id = any(%s::uuid[]))
            """,
            (occurrence_id, ids),
        )
        for ordre, pid in enumerate(ids):
            cur.execute(
                """
                insert into bancarisation.occurrence_responsable
                  (occurrence_id, personne_id, ordre)
                values (%s, %s, %s)
                on conflict (occurrence_id, personne_id)
                do update set ordre = excluded.ordre
                """,
                (occurrence_id, pid, ordre),
            )
            cur.execute(
                """
                insert into bancarisation.projet_contact (projet_id, personne_id, role_code)
                select o.projet_id, p.id,
                  case
                    when p.prestataire_id is not null then 'prestataire_travaux'
                    else 'operateur_compensation'
                  end
                from bancarisation.occurrence o
                join bancarisation.personnes p on p.id = %s
                where o.id = %s
                  and not exists (
                    select 1 from bancarisation.projet_contact pc
                    where pc.projet_id = o.projet_id
                      and pc.personne_id = p.id
                  )
                """,
                (pid, occurrence_id),
            )
    else:
        cur.execute(
            "delete from bancarisation.occurrence_responsable where occurrence_id = %s",
            (occurrence_id,),
        )
    cur.execute(
        """
        update bancarisation.occurrence
        set responsable_id = %s
        where id = %s
        """,
        (ids[0] if ids else None, occurrence_id),
    )


def lire_responsables(cur: psycopg.Cursor, occurrence_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not occurrence_ids:
        return {}
    cur.execute(_SQL_LISTE, (occurrence_ids,))
    colonnes = [d.name for d in cur.description or []]
    par: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cur.fetchall():
        item = dict(row) if isinstance(row, dict) else dict(zip(colonnes, row))
        occ = str(item.pop("occurrence_id"))
        item.pop("ordre", None)
        item["personne_id"] = str(item["personne_id"])
        par[occ].append(item)
    return par


def attacher_responsables(rows: list[dict[str, Any]]) -> None:
    """Ajoute la clé `responsables` sur chaque ligne d'occurrence."""
    ids = [str(r["id"]) for r in rows if r.get("id")]
    if not ids:
        return
    from api.db.utilisateur import connect_utilisateur

    with connect_utilisateur() as conn:
        with conn.cursor() as cur:
            par = lire_responsables(cur, ids)
    for row in rows:
        occ = str(row.get("id") or "")
        liste = par.get(occ) or []
        if not liste and row.get("responsable_id"):
            liste = [{
                "personne_id": str(row["responsable_id"]),
                "nom": row.get("responsable_nom"),
                "fonction": None,
                "role_libelle": None,
                "role_categorie": None,
            }]
        row["responsables"] = liste
