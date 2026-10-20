"""Invitations et organisations : droits en Python, écritures hors RLS."""

from __future__ import annotations

import html
import os
import smtplib
from email.message import EmailMessage
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from psycopg.rows import dict_row
from psycopg.types.json import Json

from api.db.env import get_database_url
from api.db.supabase import get_supabase_admin
from api.db.utilisateur import connect_utilisateur_dict

ROLES = ("admin", "membre")
PORTEES = ("entite", "branche")
TYPES_INVITATION = ("bureau", "partenaire")
NIVEAUX_ACCES = ("lecteur", "contributeur", "gestionnaire")
QUALITES_ACCES = (
    "operateur",
    "maitre_ouvrage",
    "bureau_etudes",
    "controle",
    "ancien_proprietaire",
    "prestataire",
)
_RANG = {"membre": 1, "admin": 2}
_SITE = "https://bancarisation.kerelia.fr"


def normaliser_email(email: str) -> str:
    valeur = email.strip().lower()
    if "@" not in valeur or valeur.startswith("@") or valeur.endswith("@"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Adresse e-mail invalide.")
    return valeur


def statut_invitation(revoquee_le: Any, last_sign_in_at: Any) -> str:
    if revoquee_le is not None:
        return "revoquee"
    if last_sign_in_at is not None:
        return "acceptee"
    return "en_attente"


def _exiger_admin_plateforme() -> UUID:
    with connect_utilisateur_dict() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT utilisateur_id, admin_plateforme
                FROM bancarisation.profils
                WHERE utilisateur_id = auth.uid()
                """
            )
            row = cur.fetchone()
    if not row or not row.get("admin_plateforme"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé à l'administration plateforme.")
    return UUID(str(row["utilisateur_id"]))


def _role_acteur(organisation_id: UUID) -> str | None:
    with connect_utilisateur_dict() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT prive.role_org(%s) AS role", (str(organisation_id),))
            row = cur.fetchone()
    if not row:
        return None
    return row.get("role")


def _exiger_admin_org_ou_plateforme(organisation_id: UUID) -> UUID:
    with connect_utilisateur_dict() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT utilisateur_id, admin_plateforme
                FROM bancarisation.profils
                WHERE utilisateur_id = auth.uid()
                """
            )
            profil = cur.fetchone()
    if not profil:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé à un administrateur.")
    acteur = UUID(str(profil["utilisateur_id"]))
    if profil.get("admin_plateforme"):
        return acteur
    if _role_acteur(organisation_id) != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé à l'admin de l'organisation.")
    return acteur


def _verifier_role_attribuable(organisation_id: UUID, role: str, admin_plateforme: bool) -> None:
    if role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Rôle inconnu.")
    if admin_plateforme:
        return
    role_acteur = _role_acteur(organisation_id)
    if role_acteur not in _RANG or _RANG[role] > _RANG[role_acteur]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Ce rôle est supérieur au vôtre.",
        )


def _connect():
    return psycopg_connect()


def psycopg_connect():
    import psycopg

    return psycopg.connect(get_database_url(), row_factory=dict_row)


def _utilisateur_par_email(cur, email: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT id, last_sign_in_at
        FROM auth.users
        WHERE lower(email) = %s
        """,
        (email,),
    )
    return cur.fetchone()


def _nom_organisation(cur, organisation_id: UUID) -> str:
    cur.execute(
        "SELECT nom FROM bancarisation.organisations WHERE id = %s",
        (str(organisation_id),),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organisation introuvable.")
    return str(row["nom"])


def _inviter_compte(email: str, nom_org: str) -> str:
    client = get_supabase_admin()
    res = client.auth.admin.invite_user_by_email(
        email,
        {
            "data": {"organisation_nom": nom_org},
            "redirect_to": f"{_SITE}/auth/activer",
        },
    )
    user = getattr(res, "user", None) or (res if isinstance(res, dict) else {})
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    if not uid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitation Auth sans identifiant.")
    return str(uid)


def _supprimer_compte(utilisateur_id: str) -> None:
    get_supabase_admin().auth.admin.delete_user(utilisateur_id)


def _envoyer_acces(email: str, nom_org: str) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    sender = os.environ.get("SMTP_ADMIN_EMAIL", "nepasrepondre@kerelia.fr").strip()
    port = int(os.environ.get("SMTP_PORT", "587") or "587")
    if not host or not user or not password:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Envoi du mail impossible : SMTP non configuré.",
        )
    msg = EmailMessage()
    nom_html = html.escape(nom_org)
    msg["Subject"] = f"Accès à l'espace {nom_org}"
    msg["From"] = sender
    msg["To"] = email
    texte = (
        f"Vous avez désormais accès à l'espace {nom_org}.\n"
        f"Ouvrez {_SITE} et connectez-vous avec votre compte existant.\n"
    )
    corps = f"""<!DOCTYPE html>
<html lang="fr"><body style="margin:0;padding:0;background:#f4f6f4;font-family:Georgia,serif;color:#1c241c;">
<table role="presentation" width="100%" style="background:#f4f6f4;padding:32px 12px;"><tr><td align="center">
<table role="presentation" width="100%" style="max-width:520px;background:#fff;border:1px solid #e2e8e2;border-radius:12px;">
<tr><td style="padding:28px 32px 8px;font-family:Arial,sans-serif;font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:#3d6b4f;">KerERC</td></tr>
<tr><td style="padding:8px 32px 0;font-size:26px;">Nouvel accès</td></tr>
<tr><td style="padding:16px 32px 0;font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#3a433a;">
Vous avez désormais accès à l'espace <strong>{nom_html}</strong>.</td></tr>
<tr><td style="padding:24px 32px 28px;">
<a href="{_SITE}" style="display:inline-block;background:#245c3a;color:#fff;font-family:Arial,sans-serif;font-size:15px;font-weight:600;text-decoration:none;padding:12px 20px;border-radius:8px;">Ouvrir KerERC</a>
</td></tr></table></td></tr></table></body></html>"""
    msg.set_content(texte)
    msg.add_alternative(corps, subtype="html")
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


def inviter(
    email: str,
    organisation_id: UUID,
    role: str | None,
    portee: str,
    invite_par: UUID,
    *,
    cur=None,
    compte_deja_cree: str | None = None,
    type_personne: str = "bureau",
    projets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rattache l'utilisateur (sauf partenaire) puis enregistre l'invitation et les accès.

    Si ``cur`` est fourni, l'appelant possède la transaction (création d'organisation).
    """
    email_n = normaliser_email(email)
    if type_personne not in TYPES_INVITATION:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Type d'invitation inconnu.")
    if portee not in PORTEES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Portée inconnue.")
    lignes = _normaliser_projets(projets or [])
    if type_personne == "partenaire":
        role = None
        if not lignes:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Un partenaire doit recevoir au moins un projet.",
            )
    elif role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Rôle inconnu.")
    if role == "admin":
        lignes = []

    propre = cur is None
    conn = _connect() if propre else None
    curseur = cur if cur is not None else conn.cursor()
    nouveau_compte = False
    cree_ici: str | None = None
    try:
        nom = _nom_organisation(curseur, organisation_id)
        _verifier_projets_organisation(curseur, organisation_id, lignes)
        existant = _utilisateur_par_email(curseur, email_n)
        if compte_deja_cree:
            uid = compte_deja_cree
            nouveau_compte = True
        elif existant:
            uid = str(existant["id"])
            if _est_membre(curseur, uid, organisation_id):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Cette personne est déjà membre de l'organisation.",
                )
            if existant["last_sign_in_at"] is None:
                _inviter_compte(email_n, nom)
            else:
                _envoyer_acces(email_n, nom)
        else:
            uid = _inviter_compte(email_n, nom)
            nouveau_compte = True
            cree_ici = uid

        if type_personne == "bureau":
            curseur.execute(
                """
                INSERT INTO bancarisation.membre_organisation
                    (utilisateur_id, organisation_id, role, portee, statut, invite_par)
                VALUES (%s, %s, %s, %s, 'actif', %s)
                """,
                (uid, str(organisation_id), role, portee, str(invite_par)),
            )
        curseur.execute(
            """
            INSERT INTO bancarisation.invitation
                (email, organisation_id, role, portee, utilisateur_id, invite_par,
                 nouveau_compte, type, projets)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                email_n,
                str(organisation_id),
                role,
                portee,
                uid,
                str(invite_par),
                nouveau_compte,
                type_personne,
                Json(lignes),
            ),
        )
        invitation_id = curseur.fetchone()["id"]
        for ligne in lignes:
            _inserer_acces(curseur, ligne, uid, invite_par)
        if propre:
            conn.commit()
        return {
            "id": str(invitation_id),
            "email": email_n,
            "utilisateur_id": uid,
            "nouveau_compte": nouveau_compte,
            "statut": "en_attente",
            "type": type_personne,
            "projets": lignes,
        }
    except Exception:
        if propre and conn is not None:
            conn.rollback()
        if cree_ici:
            try:
                _supprimer_compte(cree_ici)
            except Exception:
                pass
        raise
    finally:
        if propre and conn is not None:
            conn.close()


def _normaliser_projets(projets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lignes = []
    for brut in projets:
        niveau = brut.get("niveau")
        qualite = brut.get("qualite")
        if niveau not in NIVEAUX_ACCES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Niveau inconnu.")
        if qualite is not None and qualite not in QUALITES_ACCES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Qualité inconnue.")
        lignes.append({
            "projet_id": str(brut["projet_id"]),
            "niveau": niveau,
            "voir_finances": bool(brut.get("voir_finances")),
            "qualite": qualite,
        })
    return lignes


def _verifier_projets_organisation(cur, organisation_id: UUID, lignes: list[dict[str, Any]]) -> None:
    for ligne in lignes:
        cur.execute(
            """
            SELECT 1 FROM bancarisation.projets
            WHERE id = %s AND organisation_id = %s
            """,
            (ligne["projet_id"], str(organisation_id)),
        )
        if not cur.fetchone():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Un des projets n'appartient pas à cette organisation.",
            )


def _est_membre(cur, utilisateur_id: str, organisation_id: UUID) -> bool:
    cur.execute(
        """
        SELECT 1 FROM bancarisation.membre_organisation
        WHERE utilisateur_id = %s AND organisation_id = %s
        """,
        (utilisateur_id, str(organisation_id)),
    )
    return cur.fetchone() is not None


def _inserer_acces(cur, ligne: dict[str, Any], utilisateur_id: str, accorde_par: UUID) -> None:
    cur.execute(
        """
        SELECT 1 FROM bancarisation.projet_acces
        WHERE projet_id = %s AND utilisateur_id = %s
        """,
        (ligne["projet_id"], utilisateur_id),
    )
    if cur.fetchone():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cette personne a déjà un accès sur l'un des projets.",
        )
    cur.execute(
        """
        INSERT INTO bancarisation.projet_acces
            (projet_id, utilisateur_id, qualite, niveau, voir_finances, accorde_par)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            ligne["projet_id"],
            utilisateur_id,
            ligne["qualite"],
            ligne["niveau"],
            ligne["voir_finances"],
            str(accorde_par),
        ),
    )


def creer_organisation(
    nom: str,
    type_org: str,
    parent_id: UUID | None,
    manager_email: str,
    nature: str = "bureau_etudes",
) -> dict[str, Any]:
    acteur = _exiger_admin_plateforme()
    email_n = normaliser_email(manager_email)
    conn = _connect()
    cree: str | None = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bancarisation.organisations (nom, type, nature, statut, parent_id)
                VALUES (%s, %s, %s, 'active', %s)
                RETURNING id, nom, type, nature, parent_id, statut
                """,
                (nom.strip(), type_org, nature, str(parent_id) if parent_id else None),
            )
            org = dict(cur.fetchone())
            org_id = UUID(str(org["id"]))
            if _utilisateur_par_email(cur, email_n) is None:
                cree = _inviter_compte(email_n, str(org["nom"]))
            resultat = inviter(
                email_n,
                org_id,
                "admin",
                "entite",
                acteur,
                cur=cur,
                compte_deja_cree=cree,
            )
            cur.execute(
                """
                INSERT INTO bancarisation.journal_audit
                    (acteur_id, organisation_id, action, details)
                VALUES (%s, %s, 'admin.organisation.creer', jsonb_build_object('nom', %s))
                """,
                (str(acteur), str(org_id), nom.strip()),
            )
        conn.commit()
        org["id"] = str(org["id"])
        if org.get("parent_id"):
            org["parent_id"] = str(org["parent_id"])
        org["invitation"] = resultat
        return org
    except Exception:
        conn.rollback()
        if cree:
            try:
                _supprimer_compte(cree)
            except Exception:
                pass
        raise
    finally:
        conn.close()


def lister_organisations() -> list[dict[str, Any]]:
    _exiger_admin_plateforme()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.nom, o.type, o.nature, o.statut, o.parent_id,
                       coalesce(mb.nb_membres, 0) AS nb_membres,
                       coalesce(mb.membres, '[]'::json) AS membres,
                       (SELECT count(*) FROM bancarisation.projets p
                         WHERE p.organisation_id = o.id) AS nb_projets
                FROM bancarisation.organisations o
                LEFT JOIN LATERAL (
                  SELECT count(*) AS nb_membres,
                         json_agg(json_build_object(
                           'utilisateur_id', m.utilisateur_id,
                           'email', u.email,
                           'role', m.role,
                           'statut', m.statut
                         ) ORDER BY u.email) AS membres
                  FROM bancarisation.membre_organisation m
                  JOIN auth.users u ON u.id = m.utilisateur_id
                  WHERE m.organisation_id = o.id
                ) mb ON true
                ORDER BY o.nom
                """
            )
            rows = []
            for row in cur.fetchall():
                item = dict(row)
                item["id"] = str(item["id"])
                if item.get("parent_id"):
                    item["parent_id"] = str(item["parent_id"])
                item["nb_membres"] = int(item["nb_membres"])
                item["nb_projets"] = int(item["nb_projets"])
                item["membres"] = item["membres"] or []
                item["invitations_en_attente"] = 0
                rows.append(item)
            try:
                cur.execute(
                    """
                    SELECT i.organisation_id::text AS organisation_id, count(*) AS n
                    FROM bancarisation.invitation i
                    LEFT JOIN auth.users u ON u.id = i.utilisateur_id
                    WHERE i.revoquee_le IS NULL
                      AND u.last_sign_in_at IS NULL
                    GROUP BY i.organisation_id
                    """
                )
                en_attente = {r["organisation_id"]: int(r["n"]) for r in cur.fetchall()}
                for item in rows:
                    item["invitations_en_attente"] = en_attente.get(item["id"], 0)
            except Exception:
                conn.rollback()
            return rows
    finally:
        conn.close()


def lister_membres(organisation_id: UUID) -> list[dict[str, Any]]:
    _exiger_admin_org_ou_plateforme(organisation_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.utilisateur_id, u.email,
                       nullif(trim(concat_ws(' ', p.prenom, p.nom)), '') AS nom,
                       m.role, m.portee, u.last_sign_in_at
                FROM bancarisation.membre_organisation m
                JOIN auth.users u ON u.id = m.utilisateur_id
                LEFT JOIN bancarisation.profils p ON p.utilisateur_id = m.utilisateur_id
                WHERE m.organisation_id = %s
                ORDER BY u.email
                """,
                (str(organisation_id),),
            )
            rows = []
            for row in cur.fetchall():
                item = dict(row)
                item["utilisateur_id"] = str(item["utilisateur_id"])
                if item.get("last_sign_in_at"):
                    item["last_sign_in_at"] = item["last_sign_in_at"].isoformat()
                rows.append(item)
            return rows
    finally:
        conn.close()


def lister_invitations(organisation_id: UUID) -> list[dict[str, Any]]:
    _exiger_admin_org_ou_plateforme(organisation_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.id, i.email, i.role, i.portee, i.nouveau_compte, i.type, i.projets,
                       i.revoquee_le, i.created_at, u.last_sign_in_at
                FROM bancarisation.invitation i
                LEFT JOIN auth.users u ON u.id = i.utilisateur_id
                WHERE i.organisation_id = %s
                ORDER BY i.created_at DESC
                """,
                (str(organisation_id),),
            )
            rows = []
            for row in cur.fetchall():
                item = {
                    "id": str(row["id"]),
                    "email": row["email"],
                    "role": row["role"],
                    "portee": row["portee"],
                    "nouveau_compte": row["nouveau_compte"],
                    "type": row.get("type") or "bureau",
                    "projets": row.get("projets") or [],
                    "statut": statut_invitation(row["revoquee_le"], row["last_sign_in_at"]),
                    "created_at": row["created_at"].isoformat(),
                }
                rows.append(item)
            return rows
    finally:
        conn.close()


def _charger_invitation(invitation_id: UUID) -> dict[str, Any]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.*, u.last_sign_in_at, o.nom AS organisation_nom
                FROM bancarisation.invitation i
                LEFT JOIN auth.users u ON u.id = i.utilisateur_id
                JOIN bancarisation.organisations o ON o.id = i.organisation_id
                WHERE i.id = %s
                """,
                (str(invitation_id),),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation introuvable.")
    return dict(row)


def renvoyer(invitation_id: UUID) -> dict[str, Any]:
    row = _charger_invitation(invitation_id)
    _exiger_admin_org_ou_plateforme(UUID(str(row["organisation_id"])))
    statut = statut_invitation(row["revoquee_le"], row["last_sign_in_at"])
    if statut != "en_attente" or not row["nouveau_compte"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Seule une invitation de nouveau compte encore en attente peut être renvoyée.",
        )
    email = row["email"]
    nom = row["organisation_nom"]
    try:
        _inviter_compte(email, nom)
    except Exception:
        client = get_supabase_admin()
        lien = client.auth.admin.generate_link({"type": "invite", "email": email})
        props = getattr(lien, "properties", None) or {}
        token = getattr(props, "hashed_token", None) or (
            props.get("hashed_token") if isinstance(props, dict) else None
        )
        if not token:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Renvoi de l'invitation impossible.")
        lien_activer = f"{_SITE}/auth/activer?token_hash={token}&type=invite"
        _envoyer_lien_invite(email, nom, lien_activer)
    return {"ok": True}


def _envoyer_lien_invite(email: str, nom_org: str, lien: str) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    sender = os.environ.get("SMTP_ADMIN_EMAIL", "nepasrepondre@kerelia.fr").strip()
    port = int(os.environ.get("SMTP_PORT", "587") or "587")
    msg = EmailMessage()
    msg["Subject"] = "Activer votre compte KerERC"
    msg["From"] = sender
    msg["To"] = email
    nom_html = html.escape(nom_org)
    lien_html = html.escape(lien, quote=True)
    msg.set_content(f"Vous êtes invité(e) à rejoindre l'espace {nom_org}.\n{lien}\n")
    msg.add_alternative(
        f"<p>Vous êtes invité(e) à rejoindre l'espace <strong>{nom_html}</strong>.</p>"
        f'<p><a href="{lien_html}">Activer mon compte</a></p>',
        subtype="html",
    )
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


def revoquer(invitation_id: UUID) -> dict[str, Any]:
    row = _charger_invitation(invitation_id)
    _exiger_admin_org_ou_plateforme(UUID(str(row["organisation_id"])))
    if row["revoquee_le"] is not None:
        return {"ok": True}
    uid = str(row["utilisateur_id"]) if row.get("utilisateur_id") else None
    conn = _connect()
    supprimer = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bancarisation.invitation SET revoquee_le = now() WHERE id = %s",
                (str(invitation_id),),
            )
            if uid:
                cur.execute(
                    """
                    DELETE FROM bancarisation.membre_organisation
                    WHERE utilisateur_id = %s AND organisation_id = %s
                    """,
                    (uid, str(row["organisation_id"])),
                )
                for ligne in row.get("projets") or []:
                    projet_id = ligne.get("projet_id") if isinstance(ligne, dict) else None
                    if projet_id:
                        cur.execute(
                            """
                            DELETE FROM bancarisation.projet_acces
                            WHERE projet_id = %s AND utilisateur_id = %s
                            """,
                            (str(projet_id), uid),
                        )
                if row["nouveau_compte"] and row["last_sign_in_at"] is None:
                    cur.execute(
                        """
                        SELECT 1 FROM bancarisation.membre_organisation
                        WHERE utilisateur_id = %s
                        """,
                        (uid,),
                    )
                    supprimer = cur.fetchone() is None
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if supprimer and uid:
        _supprimer_compte(uid)
    return {"ok": True}


def changer_role_membre(organisation_id: UUID, utilisateur_id: UUID, role: str) -> dict[str, Any]:
    _exiger_admin_org_ou_plateforme(organisation_id)
    if role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Rôle inconnu.")
    with connect_utilisateur_dict() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT admin_plateforme
                FROM bancarisation.profils
                WHERE utilisateur_id = auth.uid()
                """
            )
            profil = cur.fetchone()
    _verifier_role_attribuable(
        organisation_id,
        role,
        bool(profil and profil.get("admin_plateforme")),
    )
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role FROM bancarisation.membre_organisation
                WHERE organisation_id = %s AND utilisateur_id = %s
                """,
                (str(organisation_id), str(utilisateur_id)),
            )
            actuel = cur.fetchone()
            if not actuel:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Membre introuvable.")
            if actuel["role"] == "admin" and role == "membre":
                cur.execute(
                    """
                    SELECT count(*) AS n FROM bancarisation.membre_organisation
                    WHERE organisation_id = %s AND role = 'admin'
                    """,
                    (str(organisation_id),),
                )
                if int(cur.fetchone()["n"]) <= 1:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "Il doit rester au moins un administrateur.",
                    )
            cur.execute(
                """
                UPDATE bancarisation.membre_organisation
                SET role = %s
                WHERE organisation_id = %s AND utilisateur_id = %s
                """,
                (role, str(organisation_id), str(utilisateur_id)),
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "role": role}
