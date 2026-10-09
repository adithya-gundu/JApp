from email.message import EmailMessage

import pytest

from jobhunt import email_watcher
from jobhunt.email_watcher import detect_status, match_application, process_message, watch_emails


@pytest.mark.parametrize("subject, body, expected", [
    ("Interview invitation", "We'd like to schedule a call.", "Phone Screen"),
    ("Next steps", "Please complete this technical assessment.", "Technical Interview"),
    ("Your offer from Acme", "We are pleased to share the details.", "Offer Received"),
    ("Update on your application", "Unfortunately we will not proceed.", "Rejected"),
    ("Thanks for your interview", "We regret to inform you the role is filled.", "Rejected"),
    ("Update", "We have decided not to move forward with your application.", "Rejected"),
    ("Update", "We'd love to move forward with you.", "Phone Screen"),
    ("Newsletter", "We are offering a new product line.", None),
])
def test_detect_status(subject, body, expected):
    assert detect_status(subject, body) == expected


def test_match_application_by_domain_display_name_and_body():
    records = [{"id": "1", "company": "Acme"}, {"id": "2", "company": "Globex"}]
    assert match_application(records, "Jane <jane@acme.com>", "Hello", "")["id"] == "1"
    assert match_application(records, "Globex Recruiting <no-reply@greenhouse.io>", "Hi", "")["id"] == "2"
    assert match_application(records, "ATS <no-reply@lever.co>", "Update", "Thanks for applying to Globex.")["id"] == "2"
    assert match_application(records, "Friend <pal@gmail.com>", "Lunch?", "See you soon") is None


def _message(sender, subject, body, message_id):
    msg = EmailMessage()
    msg["From"], msg["Subject"], msg["Message-ID"] = sender, subject, message_id
    msg.set_content(body)
    return msg


def test_process_message_updates_status_and_logs(tracker):
    record = tracker.create({"title": "Dev", "company": "Acme", "status": "Applied"})
    result = process_message(tracker, _message("Jane <jane@acme.com>", "Interview availability", "Hi", "<1>"))

    assert result["status"] == "Phone Screen"
    saved = tracker.get(record["id"])
    assert saved["status"] == "Phone Screen"
    assert "Interview availability" in saved["email_log"]


def test_status_never_moves_backwards(tracker):
    record = tracker.create({"title": "Dev", "company": "Acme", "status": "Final Round"})
    result = process_message(tracker, _message("jane@acme.com", "Next steps", "Details inside", "<2>"))

    assert result["status"] is None
    assert tracker.get(record["id"])["status"] == "Final Round"

    process_message(tracker, _message("jane@acme.com", "Update", "Unfortunately...", "<3>"))
    assert tracker.get(record["id"])["status"] == "Rejected"


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages
        self.fetch_specs = []

    def __call__(self, host):
        return self

    def login(self, user, password):
        pass

    def select(self, folder, readonly=False):
        assert readonly

    def search(self, charset, criterion):
        return "OK", [" ".join(str(i + 1) for i in range(len(self.messages))).encode()]

    def fetch(self, msg_id, spec):
        self.fetch_specs.append(spec)
        return "OK", [(b"1 (BODY[]", self.messages[int(msg_id) - 1].as_bytes()), b")"]

    def logout(self):
        pass


def test_watch_emails_handles_each_message_once(tracker, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(email_watcher, "STATE_PATH", tmp_path / "processed.json")
    record = tracker.create({"title": "Dev", "company": "Acme", "status": "Applied"})
    imap = FakeIMAP([
        _message("Jane <jane@acme.com>", "Interview", "Let's talk", "<a>"),
        _message("Shop <deals@shop.com>", "Sale", "50% off", "<b>"),
    ])

    first = watch_emails(tracker, settings, imap_factory=imap)
    assert [r["status"] for r in first] == ["Phone Screen"]
    assert all("PEEK" in spec for spec in imap.fetch_specs)  # mail stays unread

    assert watch_emails(tracker, settings, imap_factory=imap) == []
    assert tracker.get(record["id"])["email_log"].count("Interview") == 1
