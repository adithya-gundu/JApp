from datetime import date, timedelta

from jobhunt.tracker import NotionTracker


def test_applied_sets_dates_and_auto_apply_flags(tracker):
    record = tracker.create({"title": "Backend Engineer", "company": "Acme", "url": "https://acme.test/jobs/1"})
    assert record["status"] == "Saved"

    updated = tracker.set_status(record["id"], "Applied", follow_up_days=7, auto_applied=True)
    assert updated["date_applied"] == date.today().isoformat()
    assert updated["follow_up"] == (date.today() + timedelta(days=7)).isoformat()
    assert updated["auto_applied"] is True
    assert updated["source"] == "LLM+Browser Auto-Apply"


def test_later_status_keeps_applied_date(tracker):
    record = tracker.create({"title": "Dev", "company": "Acme", "date_applied": "2026-01-05"})
    updated = tracker.set_status(record["id"], "Phone Screen")
    assert updated["date_applied"] == "2026-01-05"
    assert "auto_applied" not in updated


def test_log_email_appends(tracker):
    record = tracker.create({"title": "Dev", "company": "Acme"})
    tracker.log_email(record["id"], "first")
    log = tracker.log_email(record["id"], "second")["email_log"]
    assert log.count("\n") == 2 and "first" in log and log.rstrip().endswith("second")


def test_find_and_followups(tracker):
    record = tracker.create({"title": "Dev", "company": "Acme Corp", "url": "https://acme.test/jobs/1/"})
    assert tracker.find_by_url("https://acme.test/jobs/1")["id"] == record["id"]
    assert tracker.find_by_company("acme")["id"] == record["id"]
    assert tracker.find_by_company("globex") is None

    tracker.set_status(record["id"], "Applied", follow_up_days=7)
    assert tracker.due_followups() == []
    assert len(tracker.due_followups(today=date.today() + timedelta(days=7))) == 1


class FakeNotion:
    """Stands in for notion_client.Client; records requests."""

    SCHEMA = {
        "Job Title": "title", "Status": "status", "Company": "rich_text", "Date Applied": "date",
        "Follow-up Date": "date", "Email Log": "rich_text", "Auto-Applied (LLM)?": "checkbox",
        "Source": "select", "Tech Stack": "multi_select", "Job Posting URL": "url",
    }

    def __init__(self):
        self.calls = []

    def request(self, path, method, body=None):
        self.calls.append((method, path, body))
        if path.startswith("databases/") and method == "GET":
            return {"properties": {name: {"type": t} for name, t in self.SCHEMA.items()}}
        return {"id": "page-1", "properties": {
            "Job Title": {"type": "title", "title": [{"plain_text": "Dev"}]},
            "Status": {"type": "status", "status": {"name": "Applied"}},
            "Tech Stack": {"type": "multi_select", "multi_select": [{"name": "Python"}]},
        }}


def test_notion_writes_each_property_in_its_schema_type():
    client = FakeNotion()
    tracker = NotionTracker("token", "db-1", client=client)
    record = tracker.set_status("page-1", "Applied", auto_applied=True)

    method, path, body = client.calls[-1]
    assert (method, path) == ("PATCH", "pages/page-1")
    props = body["properties"]
    assert props["Status"] == {"status": {"name": "Applied"}}
    assert props["Date Applied"] == {"date": {"start": date.today().isoformat()}}
    assert props["Auto-Applied (LLM)?"] == {"checkbox": True}
    assert props["Source"] == {"select": {"name": "LLM+Browser Auto-Apply"}}
    assert record == {"id": "page-1", "title": "Dev", "status": "Applied", "tech_stack": ["Python"]}


def test_notion_skips_missing_properties_and_chunks_long_text():
    client = FakeNotion()
    tracker = NotionTracker("token", "db-1", client=client)
    tracker.update("page-1", {"email_log": "x" * 4500, "excitement": 5, "tech_stack": ["Python", "Go"]})

    props = client.calls[-1][2]["properties"]
    assert "Excitement Level" not in props  # not in this database's schema
    assert [len(part["text"]["content"]) for part in props["Email Log"]["rich_text"]] == [2000, 2000, 500]
    assert props["Tech Stack"] == {"multi_select": [{"name": "Python"}, {"name": "Go"}]}
