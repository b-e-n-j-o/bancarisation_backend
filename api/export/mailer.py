"""Mail de fin d'export. Même relais SMTP que les invitations."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("api.export.mailer")


def envoyer_export_pret(
    destinataire: str,
    nom_projet: str,
    lien: str,
    avec_avertissements: bool,
) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    sender = os.environ.get("SMTP_ADMIN_EMAIL", "nepasrepondre@kerelia.fr").strip()
    port = int(os.environ.get("SMTP_PORT", "587") or "587")
    if not host or not user or not password:
        raise RuntimeError("SMTP non configuré.")
    if not destinataire:
        raise RuntimeError("Pas d'adresse pour le demandeur.")

    precision = (
        " L'archive contient des avertissements, détaillés dans la fenêtre d'export."
        if avec_avertissements
        else ""
    )
    msg = EmailMessage()
    msg["Subject"] = f"Export du projet {nom_projet}"
    msg["From"] = sender
    msg["To"] = destinataire
    msg.set_content(
        f"L'export du projet {nom_projet} est prêt.{precision}\n"
        f"Ouvrez KerERC pour le télécharger (le lien expire sous 7 jours) :\n{lien}\n"
    )
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    logger.info("Mail d'export envoyé à %s", destinataire)
