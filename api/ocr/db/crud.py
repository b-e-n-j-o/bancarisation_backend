"""CRUD post-ingestion — Postgres identifié (RLS)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.db.utilisateur import connect_utilisateur
from api.ocr.domain.ug_ids import normalize_ug_id, normalize_ug_ids

_CHAMPS_MODIFIABLES = {
    "annee", "code", "titre", "categorie", "lib_thema", "statut", "ug_ids",
    "mois_debut", "mois_fin", "traverse_nouvel_an",
    "date_realisation", "date_realisation_fin", "surface_m2", "commentaire",
    "montant_ht", "montant_ttc", "taux_tva", "prestataire", "prestataire_id",
    "responsable_id",
    "ligne_budget_id",
    "montant_engage", "montant_realise",
}

_CHAMPS_ACTION_MODIFIABLES = {"ug_ids", "titre", "contenu_integral", "categorie", "lib_thema"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid(projet_id: UUID | str) -> str:
    return str(projet_id)


def _oid(occurrence_id: UUID | str) -> str:
    return str(occurrence_id)


def _all(sql: str, params: Any = None) -> list[dict[str, Any]]:
    with connect_utilisateur(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def _one(sql: str, params: Any = None) -> dict[str, Any] | None:
    rows = _all(sql, params)
    return rows[0] if rows else None


def get_metadata(projet_id: UUID | str) -> dict[str, Any] | None:
    return _one(
        "SELECT * FROM bancarisation.projet_metadata WHERE projet_id = %s",
        (_pid(projet_id),),
    )


def lister_actions(projet_id: UUID | str) -> list[dict[str, Any]]:
    return _all(
        """
        SELECT id, cle, code, categorie, titre, contenu_integral, ug_ids, lib_thema, confiance,
               champs_a_confirmer, avertissements
        FROM bancarisation.action_fiche
        WHERE projet_id = %s
        ORDER BY code
        """,
        (_pid(projet_id),),
    )


def lister_actions_pour_ug(
    projet_id: UUID | str,
    ug_id: str,
) -> list[dict[str, Any]]:
    ug = normalize_ug_id(ug_id)
    if not ug:
        raise ValueError("ug_id invalide.")

    pid = _pid(projet_id)
    actions = lister_actions(pid)
    occs = lister_occurrences(pid, ug_id=ug, inclure_supprimees=False)

    counts: dict[str, int] = {}
    for o in occs:
        cle = o.get("action_cle")
        if cle:
            counts[str(cle)] = counts.get(str(cle), 0) + 1

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in actions:
        cle = str(a.get("cle") or "")
        action_ugs = normalize_ug_ids(a.get("ug_ids") or [])
        linked = ug in action_ugs or cle in counts
        if not linked:
            continue
        row = dict(a)
        row["ug_ids"] = action_ugs
        row["nb_occurrences"] = counts.get(cle, 0)
        out.append(row)
        seen.add(cle)

    for cle, n in counts.items():
        if cle in seen:
            continue
        out.append({
            "id": None,
            "cle": cle,
            "code": cle,
            "categorie": "",
            "titre": cle,
            "contenu_integral": "",
            "ug_ids": [ug],
            "nb_occurrences": n,
        })

    out.sort(key=lambda r: (str(r.get("code") or ""), str(r.get("cle") or "")))
    return out


def _normaliser_code_action(code: str) -> str:
    return code.replace(" ", "").strip().upper()


_CATEGORIES_ACTION = frozenset({"TU", "TE", "SE", "MG", "EP"})


def creer_action_fiche(
    projet_id: UUID | str,
    *,
    code: str,
    categorie: str,
    titre: str,
    contenu_integral: str,
    cle: str | None = None,
    ug_ids: list[str] | None = None,
    lib_thema: str | None = None,
) -> dict[str, Any]:
    from api.ocr.extractions.catalogue.thema import normaliser_lib_thema

    code_norm = _normaliser_code_action(code)
    cat = categorie.strip().upper()
    if cat not in _CATEGORIES_ACTION:
        raise ValueError(
            f"Catégorie invalide : {categorie}. Attendu : {sorted(_CATEGORIES_ACTION)}"
        )
    titre_clean = titre.strip()
    contenu_clean = contenu_integral.strip()
    if not titre_clean:
        raise ValueError("Le titre est obligatoire.")
    if not contenu_clean:
        raise ValueError("Le contenu est obligatoire.")

    cle_norm = _normaliser_code_action(cle or code_norm)
    ugs = normalize_ug_ids(ug_ids)
    thema = normaliser_lib_thema(lib_thema)

    existing = _one(
        "SELECT id FROM bancarisation.action_fiche WHERE projet_id = %s AND cle = %s",
        (_pid(projet_id), cle_norm),
    )
    if existing:
        raise ValueError(f"Une fiche avec le code {cle_norm} existe déjà sur ce projet.")

    fiche_json = {
        "id": cle_norm,
        "code": code_norm,
        "categorie": cat,
        "titre": titre_clean,
        "lib_thema": thema,
        "contenu_integral": contenu_clean,
        "ug_ids": ugs,
        "confiance": 1.0,
        "champs_a_confirmer": [],
        "avertissements": ["Fiche créée manuellement"],
    }
    row = _one(
        """
        INSERT INTO bancarisation.action_fiche
            (projet_id, cle, code, categorie, titre, contenu_integral, fiche_json,
             ug_ids, lib_thema, confiance, champs_a_confirmer, avertissements)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1.0, %s, %s)
        RETURNING *
        """,
        (
            _pid(projet_id),
            cle_norm,
            code_norm,
            cat,
            titre_clean,
            contenu_clean,
            Jsonb(fiche_json),
            ugs,
            thema,
            [],
            ["Fiche créée manuellement"],
        ),
    )
    if not row:
        raise RuntimeError("Insertion fiche-action échouée.")
    return row


def modifier_action_fiche(
    projet_id: UUID | str,
    action_id: UUID | str,
    **champs: Any,
) -> dict[str, Any] | None:
    from api.ocr.extractions.catalogue.thema import normaliser_lib_thema

    maj = {k: v for k, v in champs.items() if k in _CHAMPS_ACTION_MODIFIABLES}
    if not maj:
        raise ValueError(
            f"Aucun champ modifiable. Autorisés : {sorted(_CHAMPS_ACTION_MODIFIABLES)}"
        )
    if "ug_ids" in maj:
        maj["ug_ids"] = normalize_ug_ids(maj["ug_ids"])
    if "lib_thema" in maj:
        maj["lib_thema"] = normaliser_lib_thema(maj["lib_thema"])
    sets = ", ".join(f"{k} = %s" for k in maj)
    params = list(maj.values()) + [str(action_id), _pid(projet_id)]
    return _one(
        f"UPDATE bancarisation.action_fiche SET {sets} WHERE id = %s AND projet_id = %s RETURNING *",
        params,
    )


def lister_echeances(projet_id: UUID | str) -> list[dict[str, Any]]:
    return _all(
        """
        SELECT id, cle, action_cle, code_operation, libelle, confiance,
               champs_a_confirmer, avertissements, source_page, ug_ids
        FROM bancarisation.echeance
        WHERE projet_id = %s
        ORDER BY code_operation
        """,
        (_pid(projet_id),),
    )


_ECHEANCE_SELECT = """
id, cle, action_cle, code_operation, type_operation, type_metier, libelle,
recurrence, confiance, champs_a_confirmer, avertissements, source_page,
ug_ids, fenetre_debut, fenetre_fin, fenetre_traverse_nouvel_an
"""


def _est_a_revoir(row: dict[str, Any]) -> bool:
    confiance = float(row.get("confiance") or 0)
    champs = row.get("champs_a_confirmer") or []
    avert = row.get("avertissements") or []
    return confiance < 0.7 or len(champs) > 0 or len(avert) > 0


def _est_non_placable(row: dict[str, Any]) -> bool:
    rec = row.get("recurrence") or {}
    if rec.get("type") == "dependant_evenement" and not rec.get("ancrage_annee"):
        return True
    return _est_a_revoir(row)


def _lister_echeances_detail(projet_id: UUID | str) -> list[dict[str, Any]]:
    return _all(
        f"SELECT {_ECHEANCE_SELECT} FROM bancarisation.echeance WHERE projet_id = %s ORDER BY code_operation",
        (_pid(projet_id),),
    )


def echeances_non_placables(projet_id: UUID | str) -> list[dict[str, Any]]:
    echeances = _lister_echeances_detail(projet_id)
    occs = lister_occurrences(projet_id)
    placees = {str(o["echeance_id"]) for o in occs if o.get("echeance_id")}
    return [
        e for e in echeances
        if str(e.get("id")) not in placees and _est_non_placable(e)
    ]


def echeances_a_revoir(projet_id: UUID | str) -> list[dict[str, Any]]:
    return [e for e in _lister_echeances_detail(projet_id) if _est_a_revoir(e)]


def lister_occurrences(
    projet_id: UUID | str,
    *,
    annee: int | None = None,
    ug_id: str | None = None,
    inclure_supprimees: bool = True,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM bancarisation.v_occurrence_calendrier WHERE projet_id = %s"
    params: list[Any] = [_pid(projet_id)]
    if annee is not None:
        sql += " AND annee = %s"
        params.append(annee)
    if ug_id is not None:
        ug_norm = normalize_ug_id(ug_id)
        if ug_norm:
            sql += " AND %s = ANY(ug_ids)"
            params.append(ug_norm)
    if not inclure_supprimees:
        sql += " AND statut <> 'supprime'"
    sql += " ORDER BY annee, code"
    return _all(sql, params)


def creer_occurrence(projet_id: UUID | str, **champs: Any) -> dict[str, Any]:
    colonnes = {
        k: v for k, v in champs.items()
        if k in _CHAMPS_MODIFIABLES or k == "echeance_id"
    }
    if colonnes.get("echeance_id") is not None:
        colonnes["echeance_id"] = str(colonnes["echeance_id"])
    if "ug_ids" in colonnes:
        colonnes["ug_ids"] = normalize_ug_ids(colonnes["ug_ids"])
    colonnes["projet_id"] = _pid(projet_id)
    colonnes["origine"] = "user"
    keys = list(colonnes)
    placeholders = ", ".join(["%s"] * len(keys))
    row = _one(
        f"INSERT INTO bancarisation.occurrence ({', '.join(keys)}) VALUES ({placeholders}) RETURNING *",
        [colonnes[k] for k in keys],
    )
    if not row:
        raise RuntimeError("Insertion occurrence échouée.")
    return row


def modifier_occurrence(
    occurrence_id: UUID | str,
    **champs: Any,
) -> dict[str, Any] | None:
    maj = {k: v for k, v in champs.items() if k in _CHAMPS_MODIFIABLES}
    if not maj:
        raise ValueError(f"Aucun champ modifiable. Autorisés : {sorted(_CHAMPS_MODIFIABLES)}")
    if "ug_ids" in maj:
        maj["ug_ids"] = normalize_ug_ids(maj["ug_ids"])
    maj["modifie_le"] = _now_iso()
    sets = ", ".join(f"{k} = %s" for k in maj)
    params = list(maj.values()) + [_oid(occurrence_id)]
    return _one(
        f"UPDATE bancarisation.occurrence SET {sets} WHERE id = %s RETURNING *",
        params,
    )


def supprimer_occurrence(
    occurrence_id: UUID | str,
    *,
    definitif: bool = False,
) -> bool:
    if definitif:
        row = _one(
            "DELETE FROM bancarisation.occurrence WHERE id = %s RETURNING id",
            (_oid(occurrence_id),),
        )
        return bool(row)
    row = _one(
        """
        UPDATE bancarisation.occurrence
        SET statut = 'supprime', modifie_le = %s
        WHERE id = %s
        RETURNING id
        """,
        (_now_iso(), _oid(occurrence_id)),
    )
    return bool(row)
