from __future__ import annotations

import asyncio
import smtplib
import uuid
from datetime import datetime
from email.message import EmailMessage
from typing import Dict

from app.config import get_settings


async def send_email(to_address: str, subject: str, body_text: str) -> Dict[str, str]:
    return await asyncio.to_thread(_send_email_sync, to_address, subject, body_text)


def _send_email_sync(to_address: str, subject: str, body_text: str) -> Dict[str, str]:
    settings = get_settings()
    if not settings.gmail_from_address or not settings.gmail_app_password:
        raise RuntimeError("Missing Gmail SMTP credentials.")

    message = EmailMessage()
    message_id = f"<{uuid.uuid4()}@creator-sponsorship-agent>"
    message["Message-ID"] = message_id
    message["From"] = settings.gmail_from_address
    message["To"] = to_address
    message["Subject"] = subject
    message["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    message.set_content(body_text)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(settings.gmail_from_address, settings.gmail_app_password)
        server.send_message(message)

    return {"provider_message_id": message_id}
