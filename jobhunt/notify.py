"""Alerts on status changes: a JSON webhook and/or an email. Failures never stop the run."""
import json
import logging
import smtplib
import urllib.request
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, subject: str, body: str = "") -> None:
        log.info("%s%s", subject, f" - {body}" if body else "")
        if self.settings.webhook_url:
            self._webhook(subject, body)
        if self.settings.smtp_host and self.settings.notify_to:
            self._email(subject, body)

    def _webhook(self, subject: str, body: str) -> None:
        text = f"{subject}\n{body}".strip()
        # "text" is what Slack reads, "content" is what Discord reads.
        payload = json.dumps({"text": text, "content": text}).encode()
        request = urllib.request.Request(
            self.settings.webhook_url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(request, timeout=15).close()
        except OSError as e:
            log.warning("Webhook notification failed: %s", e)

    def _email(self, subject: str, body: str) -> None:
        s = self.settings
        message = EmailMessage()
        message["Subject"] = f"[Job Hunt] {subject}"
        message["From"] = s.email_address
        message["To"] = s.notify_to
        message.set_content(body or subject)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(s.email_address, s.email_password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as e:
            log.warning("Email notification failed: %s", e)
