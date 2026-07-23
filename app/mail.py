"""Transactional email for magic links (SPEC13).

Same transport as playground's `mailService`: SMTP over STARTTLS to the Yandex
postbox (`POST_SERVICE_URL`), port 587, `POST_USER`/`POST_PASS`, sending from
`MAIL_FROM`. When SMTP is unconfigured (local dev) the message — including the
magic link — is printed to the console instead, so dev login still works.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage


def send_mail(to: str, subject: str, text: str) -> None:
    host = os.getenv("POST_SERVICE_URL")
    if not host:
        print(f"[mail:dev] to={to} | {subject}\n{text}", flush=True)
        return

    msg = EmailMessage()
    msg["From"] = os.getenv("MAIL_FROM", "no-reply@js2go.ru")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)

    user = os.getenv("POST_USER")
    pwd = os.getenv("POST_PASS")
    port = int(os.getenv("POST_SERVICE_PORT", "587"))
    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=20) as server:
        server.starttls(context=context)
        if user and pwd:
            server.login(user, pwd)
        server.send_message(msg)
