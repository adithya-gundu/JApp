"""Email watcher: find replies to applications in the inbox and update the tracker."""
import email
import imaplib
import json
import logging
import re
from email.header import decode_header, make_header
from email.utils import parseaddr

from .config import ROOT, STATUS_FLOW, TERMINAL_STATUSES
from .tracker import normalize

log = logging.getLogger(__name__)

STATE_PATH = ROOT / "data" / "processed_emails.json"

# Checked in order, so a rejection that mentions "your interview" is still a rejection.
STATUS_RULES = [
    (r"unfortunately|regret to inform|not (?:to |be )?mov(?:e|ing) forward|decided to (?:move|go) forward with other",
     "Rejected"),
    (r"\boffer\b", "Offer Received"),
    (r"technical assessment", "Technical Interview"),
    (r"\binterview\b|move forward|next steps", "Phone Screen"),
]

# Mail sent through these belongs to the employer named in the display name or subject.
GENERIC_DOMAINS = {
    "gmail", "outlook", "hotmail", "yahoo", "greenhouse", "lever", "workday", "myworkday",
    "myworkdayjobs", "ashbyhq", "smartrecruiters", "icims", "jobvite", "linkedin", "indeed",
}


def detect_status(subject: str, body: str) -> str | None:
    text = f"{subject}\n{body}".lower()
    for pattern, status in STATUS_RULES:
        if re.search(pattern, text):
            return status
    return None


def _header(value) -> str:
    return str(make_header(decode_header(value))) if value else ""


def get_email_body(msg) -> str:
    """Plain-text body; falls back to the HTML part with tags stripped."""
    parts = msg.walk() if msg.is_multipart() else [msg]
    html = ""
    for part in parts:
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            return text
        if part.get_content_type() == "text/html" and not html:
            html = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", text))
    return html


def match_application(records: list[dict], sender: str, subject: str, body: str) -> dict | None:
    """Find the application an email is about, by company name."""
    display, address = parseaddr(sender)
    domain = address.rpartition("@")[2].split(".")
    org = domain[-2] if len(domain) >= 2 else ""
    header_text = normalize(f"{display} {'' if org in GENERIC_DOMAINS else org} {subject}")
    body_text = body.lower()

    candidates = [r for r in records if len(normalize(r.get("company"))) >= 3]
    for record in candidates:
        if normalize(record["company"]) in header_text:
            return record
    for record in candidates:
        if re.search(rf"\b{re.escape(record['company'].lower())}\b", body_text):
            return record
    return None


def _should_move(current: str | None, new: str) -> bool:
    """Never move an application backwards (a 'next steps' mail during final rounds)."""
    if current == new or current in ("Accepted", "Withdrawn"):
        return False
    if new in TERMINAL_STATUSES or current not in STATUS_FLOW:
        return True
    return STATUS_FLOW.index(new) > STATUS_FLOW.index(current)


def process_message(tracker, msg, notifier=None, records: list[dict] | None = None) -> dict | None:
    """Log the email against its application and update the status if it signals a change."""
    subject, sender = _header(msg["subject"]), _header(msg["from"])
    body = get_email_body(msg)
    record = match_application(tracker.list() if records is None else records, sender, subject, body)
    if not record:
        return None

    tracker.log_email(record["id"], f"Email from {sender}: '{subject}'")
    new_status = detect_status(subject, body)
    changed = bool(new_status and _should_move(record.get("status"), new_status))
    if changed:
        tracker.set_status(record["id"], new_status)
        record["status"] = new_status
        if notifier:
            notifier.send(
                f"{record.get('company')}: {record.get('title') or 'application'} -> {new_status}",
                f"From {sender}: {subject}",
            )
    return {"record": record, "status": new_status if changed else None, "subject": subject}


def _load_seen() -> list[str]:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else []


def _save_seen(seen: list[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(seen[-5000:]), encoding="utf-8")


def watch_emails(tracker, settings, notifier=None, imap_factory=imaplib.IMAP4_SSL) -> list[dict]:
    """One pass over unread mail. Messages stay unread; handled ones are remembered locally."""
    if not (settings.imap_host and settings.email_address and settings.email_password):
        raise RuntimeError("Email monitoring needs IMAP_HOST, EMAIL_ADDRESS and EMAIL_PASSWORD in .env")

    mail = imap_factory(settings.imap_host)
    mail.login(settings.email_address, settings.email_password)
    results = []
    try:
        mail.select(settings.imap_folder, readonly=True)
        _, data = mail.search(None, "UNSEEN")
        seen = _load_seen()
        seen_set = set(seen)
        records = tracker.list()
        for msg_id in data[0].split()[-200:]:
            _, msg_data = mail.fetch(msg_id, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            key = msg["message-id"] or f"{msg['date']}|{msg['from']}|{msg['subject']}"
            if key in seen_set:
                continue
            result = process_message(tracker, msg, notifier, records)
            if result:
                results.append(result)
            seen.append(key)
            seen_set.add(key)
        _save_seen(seen)
    finally:
        mail.logout()
    return results
