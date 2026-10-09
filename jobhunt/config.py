"""Settings loaded from .env / the environment, plus the tracker's property map."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Record field -> Notion property name (from the Command Center page).
PROPERTY_MAP = {
    "title": "Job Title",
    "status": "Status",
    "company": "Company",
    "date_applied": "Date Applied",
    "follow_up": "Follow-up Date",
    "next_interview": "Next Interview",
    "recruiter_email": "Recruiter Email",
    "email_log": "Email Log",
    "auto_applied": "Auto-Applied (LLM)?",
    "source": "Source",
    "excitement": "Excitement Level",
    "tech_stack": "Tech Stack",
    "location": "Location",
    "url": os.getenv("NOTION_URL_PROPERTY") or "Job Posting URL",
}

STATUS_FLOW = [
    "Saved",
    "Applied",
    "Phone Screen",
    "Technical Interview",
    "Final Round",
    "Offer Received",
    "Negotiating",
    "Accepted",
]
TERMINAL_STATUSES = ["Rejected", "Withdrawn", "No Response"]
AUTO_APPLY_SOURCE = "LLM+Browser Auto-Apply"


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Settings:
    notion_token: str = field(repr=False)  # secrets stay out of logs and tracebacks
    notion_db_id: str
    backend: str
    local_db_path: Path
    resume_path: Path
    resume_file: Path | None
    model: str
    browser_channel: str
    browser_path: str
    browser_profile_dir: Path | None
    imap_host: str
    email_address: str
    email_password: str = field(repr=False)
    imap_folder: str
    webhook_url: str
    smtp_host: str
    smtp_port: int
    notify_to: str
    follow_up_days: int


def load_settings() -> Settings:
    env = lambda name, default="": os.getenv(name, default).strip()
    token, db_id = env("NOTION_TOKEN"), env("NOTION_DB_ID")
    backend = env("TRACKER_BACKEND").lower() or ("notion" if token and db_id else "local")
    resume_file, profile_dir = env("RESUME_FILE"), env("BROWSER_PROFILE_DIR")
    return Settings(
        notion_token=token,
        notion_db_id=db_id,
        backend=backend,
        local_db_path=_path(env("LOCAL_DB_PATH", "data/applications.json")),
        resume_path=_path(env("RESUME_PATH", "resume.txt")),
        resume_file=_path(resume_file) if resume_file else None,
        model=env("CLAUDE_MODEL", "claude-opus-5-5"),
        browser_channel=env("BROWSER_CHANNEL", "msedge"),
        browser_path=env("BROWSER_PATH"),
        browser_profile_dir=_path(profile_dir) if profile_dir else None,
        imap_host=env("IMAP_HOST"),
        email_address=env("EMAIL_ADDRESS"),
        email_password=env("EMAIL_PASSWORD"),
        imap_folder=env("IMAP_FOLDER", "INBOX"),
        webhook_url=env("NOTIFY_WEBHOOK_URL"),
        smtp_host=env("SMTP_HOST"),
        smtp_port=int(env("SMTP_PORT", "587")),
        notify_to=env("NOTIFY_EMAIL_TO"),
        follow_up_days=int(env("FOLLOW_UP_DAYS", "7")),
    )
