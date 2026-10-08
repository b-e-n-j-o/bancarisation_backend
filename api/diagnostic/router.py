"""Route temporaire : retire-la une fois l'identité de connexion confirmée.

Elle renvoie l'identifiant du jeton et le nombre de projets visibles sous RLS.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.db.utilisateur import connect_utilisateur_dict

router = APIRouter(prefix="/diagnostic", tags=["diagnostic"])


@router.get("/identite")
def identite() -> dict[str, Any]:
    with connect_utilisateur_dict() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select current_database() as db,
                       inet_server_addr()::text as host,
                       current_user,
                       auth.uid()::text as uid,
                       current_setting('request.jwt.claims', true) as claims,
                       (select count(*) from bancarisation.projets) as projets,
                       (select count(*) from bancarisation.v_projet_liste_resume) as vue
                """
            )
            row = cur.fetchone()
    return dict(row or {})
