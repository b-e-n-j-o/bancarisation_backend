"""Résolution de l'auteur d'une action — placeholder jusqu'à l'auth réelle.

Les colonnes `budget_mouvement.modifie_par` et `journal_actions.acteur`
sont déjà prévues. Tant qu'il n'y a pas de session utilisateur, on lit
éventuellement un header, sinon on enregistre NULL (l'UI affiche
« non identifié »).

Quand l'auth arrivera : remplacer le corps de `acteur_depuis_headers`
par le sujet JWT / e-mail du membre, sans changer les tables ni l'UI.
"""

from __future__ import annotations

from fastapi import Header


def acteur_depuis_headers(
    x_acteur: str | None = Header(default=None, alias="X-Acteur"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> str | None:
    """Identifiant d'audit à poser sur le journal.

    Ordre : ``X-Acteur`` (libellé / e-mail) → ``X-User-Id`` (déjà utilisé
    côté parc DREAL). Absent → None.
    """
    for raw in (x_acteur, x_user_id):
        if raw and raw.strip():
            return raw.strip()[:200]
    return None
