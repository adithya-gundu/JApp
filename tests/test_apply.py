"""End-to-end apply flow in a real browser against a local form, with Claude stubbed out."""
from dataclasses import replace
from pathlib import Path

import pytest

from jobhunt import browser
from jobhunt.apply import apply_to_job

FORM_URL = (Path(__file__).parent / "fixtures" / "job_form.html").resolve().as_uri()


def fake_planner(fields, resume_text, page_text):
    by_label = {f["label"]: f["id"] for f in fields if f["type"] not in ("radio", "checkbox")}
    relocate_yes = next(f["id"] for f in fields if f["type"] == "radio" and f["option"] == "yes")
    return {
        "fills": [
            {"field_id": by_label["Full name"], "value": "Test Candidate"},
            {"field_id": by_label["Email"], "value": "candidate@example.com"},
            {"field_id": by_label["Years of experience"], "value": "3-5 years"},
            {"field_id": relocate_yes, "value": "true"},
            {"field_id": by_label["Why do you want this role?"], "value": "I enjoy building pipelines."},
            {"field_id": "f9_99", "value": "ignored"},
        ],
        "unfilled": [{"field_id": by_label["Salary expectation"], "reason": "not in resume"}],
    }


@pytest.fixture
def browser_settings(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "SCREENSHOT_DIR", tmp_path / "screenshots")
    try:
        with browser.open_page("about:blank", headless=True, **browser.launch_options(settings)):
            pass
    except RuntimeError as e:
        pytest.skip(str(e))
    return settings


def test_extract_fields_skips_hidden_and_password(browser_settings):
    with browser.open_page(FORM_URL, headless=True, **browser.launch_options(browser_settings)) as page:
        fields, _ = browser.extract_fields(page)
    assert [f["type"] for f in fields] == ["text", "email", "select", "radio", "radio", "textarea", "text"]
    assert fields[2]["options"] == ["Select...", "0-2 years", "3-5 years", "6+ years"]
    assert fields[3]["group"] == "Are you willing to relocate?"


def test_fill_without_submit_leaves_status_alone(tracker, browser_settings):
    record = tracker.create({"title": "Backend Engineer", "company": "Acme", "url": FORM_URL})
    result = apply_to_job(tracker, browser_settings, FORM_URL, "resume", headless=True, planner=fake_planner)

    assert result["filled"] == 5 and not result["submitted"]
    assert result["screenshot"].exists()
    assert any("not a field" in p for p in result["problems"])
    saved = tracker.get(record["id"])
    assert saved["status"] == "Saved"
    assert "not submitted" in saved["email_log"] and "not in resume" in saved["email_log"]


def test_submit_needs_typed_confirmation(tracker, browser_settings):
    record = tracker.create({"title": "Backend Engineer", "company": "Acme", "url": FORM_URL})
    run = lambda answer: apply_to_job(
        tracker, browser_settings, FORM_URL, "resume", submit=True, headless=True,
        planner=fake_planner, ask=lambda prompt: answer,
    )

    assert not run("yes")["submitted"]
    assert tracker.get(record["id"])["status"] == "Saved"

    assert run("SUBMIT")["submitted"]
    saved = tracker.get(record["id"])
    assert saved["status"] == "Applied" and saved["auto_applied"] is True


def test_apply_creates_a_record_for_an_untracked_url(tracker, browser_settings):
    result = apply_to_job(tracker, browser_settings, FORM_URL, "resume", headless=True, planner=fake_planner)
    assert tracker.get(result["record_id"])["title"] == "Backend Engineer at Acme - Apply"
