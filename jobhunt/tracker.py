"""Application tracker: Notion database, with a local JSON fallback.

Records are plain dicts keyed by the PROPERTY_MAP field names plus "id".
"""
from __future__ import annotations  # Tracker.list would otherwise shadow list[...] in annotations

import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import AUTO_APPLY_SOURCE, PROPERTY_MAP, Settings

log = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
_TEXT_LIMIT = 2000  # Notion's cap per rich-text object


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


class Tracker:
    """Backend-agnostic operations. Subclasses implement list/get/create/update."""

    name = "tracker"

    def list(self) -> list[dict]:
        raise NotImplementedError

    def get(self, record_id: str) -> dict:
        raise NotImplementedError

    def create(self, fields: dict) -> dict:
        raise NotImplementedError

    def update(self, record_id: str, fields: dict) -> dict:
        raise NotImplementedError

    def set_status(self, record_id: str, status: str, *, follow_up_days: int = 7,
                   auto_applied: bool = False) -> dict:
        """Update the status; moving to Applied also stamps the applied and follow-up dates."""
        fields = {"status": status}
        if status == "Applied":
            today = date.today()
            fields["date_applied"] = today.isoformat()
            fields["follow_up"] = (today + timedelta(days=follow_up_days)).isoformat()
            if auto_applied:
                fields["auto_applied"] = True
                fields["source"] = AUTO_APPLY_SOURCE
        return self.update(record_id, fields)

    def log_email(self, record_id: str, text: str) -> dict:
        """Append a timestamped entry to the Email Log field."""
        current = self.get(record_id).get("email_log") or ""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        return self.update(record_id, {"email_log": f"{current}[{stamp}] {text}\n"})

    def find_by_url(self, url: str) -> dict | None:
        target = (url or "").rstrip("/")
        return next((r for r in self.list() if (r.get("url") or "").rstrip("/") == target), None)

    def find_by_company(self, company: str) -> dict | None:
        target = normalize(company)
        if not target:
            return None
        return next((r for r in self.list() if target in normalize(r.get("company"))), None)

    def due_followups(self, today: date | None = None) -> list[dict]:
        cutoff = (today or date.today()).isoformat()
        return [
            r for r in self.list()
            if r.get("status") == "Applied" and r.get("follow_up") and r["follow_up"][:10] <= cutoff
        ]


class LocalTracker(Tracker):
    """JSON-file tracker used when Notion is not configured."""

    name = "local"

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, records: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def list(self) -> list[dict]:
        return self._load()

    def get(self, record_id: str) -> dict:
        for record in self._load():
            if record["id"] == record_id:
                return record
        raise KeyError(f"No application with id {record_id}")

    def create(self, fields: dict) -> dict:
        records = self._load()
        record = {"id": uuid.uuid4().hex, "status": "Saved", **fields}
        records.append(record)
        self._save(records)
        return record

    def update(self, record_id: str, fields: dict) -> dict:
        records = self._load()
        for record in records:
            if record["id"] == record_id:
                record.update(fields)
                self._save(records)
                return record
        raise KeyError(f"No application with id {record_id}")


class NotionTracker(Tracker):
    """Notion database tracker. Reads the database schema so each value is
    written in the shape its property type expects (status vs select, etc.)."""

    name = "notion"

    def __init__(self, token: str, db_id: str, client=None):
        if client is None:
            from notion_client import Client
            client = Client(auth=token, notion_version=NOTION_VERSION)
        self.client = client
        self.db_id = db_id
        self._schema: dict[str, str] | None = None
        self._warned: set[str] = set()

    def _request(self, path: str, method: str, body: dict | None = None) -> dict:
        import httpx
        from notion_client.errors import HTTPResponseError, RequestTimeoutError
        try:
            return self.client.request(path=path, method=method, body=body)
        except (HTTPResponseError, RequestTimeoutError, httpx.HTTPError) as e:
            raise RuntimeError(f"Notion request failed ({method} {path}): {e}") from e

    @property
    def schema(self) -> dict[str, str]:
        """Property name -> property type for the database."""
        if self._schema is None:
            db = self._request(f"databases/{self.db_id}", "GET")
            self._schema = {name: prop["type"] for name, prop in db["properties"].items()}
        return self._schema

    def _property_name(self, key: str) -> str | None:
        name = PROPERTY_MAP[key]
        if name in self.schema:
            return name
        if key == "title":  # the title column may have been renamed
            return next((n for n, t in self.schema.items() if t == "title"), None)
        if key not in self._warned:
            self._warned.add(key)
            log.warning("Notion database has no '%s' property; skipping that field.", name)
        return None

    @staticmethod
    def _encode(ptype: str, value):
        if ptype in ("title", "rich_text"):
            text = "" if value is None else str(value)
            chunks = [text[i:i + _TEXT_LIMIT] for i in range(0, len(text), _TEXT_LIMIT)]
            if ptype == "title":
                chunks = chunks[:1]
            return {ptype: [{"text": {"content": chunk}} for chunk in chunks]}
        if ptype in ("status", "select"):
            return {ptype: {"name": str(value).replace(",", " ")} if value else None}
        if ptype == "multi_select":
            values = value if isinstance(value, (list, tuple)) else str(value).split(",")
            return {ptype: [{"name": str(v).strip().replace(",", " ")} for v in values if str(v).strip()]}
        if ptype == "date":
            return {ptype: {"start": value} if value else None}
        if ptype == "checkbox":
            return {ptype: bool(value)}
        if ptype == "number":
            return {ptype: float(value) if value not in (None, "") else None}
        if ptype in ("url", "email", "phone_number"):
            return {ptype: value or None}
        raise ValueError(f"Unsupported Notion property type: {ptype}")

    @staticmethod
    def _decode(prop: dict):
        ptype = prop["type"]
        value = prop.get(ptype)
        if ptype in ("title", "rich_text"):
            return "".join(part.get("plain_text") or part["text"]["content"] for part in value)
        if ptype in ("status", "select"):
            return value["name"] if value else None
        if ptype == "multi_select":
            return [option["name"] for option in value]
        if ptype == "date":
            return value["start"] if value else None
        if ptype in ("checkbox", "number", "url", "email", "phone_number"):
            return value
        return None

    def _properties(self, fields: dict) -> dict:
        properties = {}
        for key, value in fields.items():
            name = self._property_name(key) if key in PROPERTY_MAP else None
            if name:
                properties[name] = self._encode(self.schema[name], value)
        return properties

    def _record(self, page: dict) -> dict:
        record = {"id": page["id"]}
        for key in PROPERTY_MAP:
            name = self._property_name(key)
            if name and name in page["properties"]:
                record[key] = self._decode(page["properties"][name])
        return record

    def list(self) -> list[dict]:
        records, cursor = [], None
        while True:
            body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            result = self._request(f"databases/{self.db_id}/query", "POST", body)
            records += [self._record(page) for page in result["results"]]
            if not result.get("has_more"):
                return records
            cursor = result["next_cursor"]

    def get(self, record_id: str) -> dict:
        return self._record(self._request(f"pages/{record_id}", "GET"))

    def create(self, fields: dict) -> dict:
        body = {
            "parent": {"database_id": self.db_id},
            "properties": self._properties({"status": "Saved", **fields}),
        }
        return self._record(self._request("pages", "POST", body))

    def update(self, record_id: str, fields: dict) -> dict:
        body = {"properties": self._properties(fields)}
        return self._record(self._request(f"pages/{record_id}", "PATCH", body))


def get_tracker(settings: Settings) -> Tracker:
    if settings.backend == "notion":
        if not (settings.notion_token and settings.notion_db_id):
            raise RuntimeError("TRACKER_BACKEND=notion needs NOTION_TOKEN and NOTION_DB_ID in .env")
        return NotionTracker(settings.notion_token, settings.notion_db_id)
    return LocalTracker(settings.local_db_path)
