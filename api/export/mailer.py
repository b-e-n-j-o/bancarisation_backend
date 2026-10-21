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
    *,
    taille_octets: int,
    expire_le,
    nb_avertissements: int,
    conservation_jours: int,
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
        f" {nb_avertissements} avertissement(s) sont détaillés dans la fenêtre d'export."
        if nb_avertissements
        else ""
    )
    taille = f"{taille_octets / (1024 * 1024):.1f} Mo" if taille_octets >= 1024 * 1024 else f"{max(1, taille_octets // 1024)} Ko"
    expire = expire_le.strftime("%d/%m/%Y") if hasattr(expire_le, "strftime") else str(expire_le)[:10]
    msg = EmailMessage()
    msg["Subject"] = f"Export du projet {nom_projet}"
    msg["From"] = sender
    msg["To"] = destinataire
    msg.set_content(
        f"L'export du projet {nom_projet} est prêt ({taille}).{precision}\n"
        f"Il reste téléchargeable {conservation_jours} jours, jusqu'au {expire}.\n"
        f"Ouvrez KerERC pour le télécharger :\n{lien}\n"
    )
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    logger.info("Mail d'export envoyé à %s", destinataire)
