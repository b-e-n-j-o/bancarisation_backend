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

from api.db.env import get_database_url
from api.db.supabase import get_supabase_admin
from api.db.utilisateur import connect_utilisateur_dict

ROLES = ("admin", "membre")
PORTEES = ("entite", "branche")
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
    role: str,
    portee: str,
    invite_par: UUID,
    *,
    cur=None,
    compte_deja_cree: str | None = None,
) -> dict[str, Any]:
    """Rattache l'utilisateur puis enregistre l'invitation.

    Si ``cur`` est fourni, l'appelant possède la transaction (création d'organisation).
    """
    email_n = normaliser_email(email)
    if role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Rôle inconnu.")
    if portee not in PORTEES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Portée inconnue.")

    propre = cur is None
    conn = _connect() if propre else None
    curseur = cur if cur is not None else conn.cursor()
    nouveau_compte = False
    cree_ici: str | None = None
    try:
        nom = _nom_organisation(curseur, organisation_id)
        existant = _utilisateur_par_email(curseur, email_n)
        if compte_deja_cree:
            uid = compte_deja_cree
            nouveau_compte = True
        elif existant:
            uid = str(existant["id"])
            curseur.execute(
                """
                SELECT 1 FROM bancarisation.membre_organisation
                WHERE utilisateur_id = %s AND organisation_id = %s
                """,
                (uid, str(organisation_id)),
            )
            if curseur.fetchone():
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
                (email, organisation_id, role, portee, utilisateur_id, invite_par, nouveau_compte)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (email_n, str(organisation_id), role, portee, uid, str(invite_par), nouveau_compte),
        )
        invitation_id = curseur.fetchone()["id"]
        if propre:
            conn.commit()
        return {
            "id": str(invitation_id),
            "email": email_n,
            "utilisateur_id": uid,
            "nouveau_compte": nouveau_compte,
            "statut": "en_attente",
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
                SELECT o.id, o.nom, o.type, o.nature, o.parent_id,
                       (SELECT count(*) FROM bancarisation.membre_organisation m
                         WHERE m.organisation_id = o.id AND m.statut = 'actif') AS nb_membres,
                       (SELECT count(*) FROM bancarisation.invitation i
                         LEFT JOIN auth.users u ON u.id = i.utilisateur_id
                         WHERE i.organisation_id = o.id
                           AND i.revoquee_le IS NULL
                           AND u.last_sign_in_at IS NULL) AS invitations_en_attente
                FROM bancarisation.organisations o
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
                item["invitations_en_attente"] = int(item["invitations_en_attente"])
                rows.append(item)
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
                SELECT m.utilisateur_id, u.email, m.role, m.portee, u.last_sign_in_at
                FROM bancarisation.membre_organisation m
                JOIN auth.users u ON u.id = m.utilisateur_id
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
                SELECT i.id, i.email, i.role, i.portee, i.nouveau_compte,
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
