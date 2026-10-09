from dataclasses import replace

import pytest

from jobhunt.config import load_settings
from jobhunt.tracker import LocalTracker


@pytest.fixture
def tracker(tmp_path):
    return LocalTracker(tmp_path / "applications.json")


@pytest.fixture
def settings(tmp_path):
    return replace(
        load_settings(), backend="local", local_db_path=tmp_path / "applications.json",
        notion_token="", notion_db_id="", browser_profile_dir=None,
        imap_host="imap.example.com", email_address="me@example.com", email_password="test-password",
        webhook_url="", smtp_host="", resume_file=None,
    )
