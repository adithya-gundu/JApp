"""Playwright helpers: open a page, read its form fields, fill them, screenshot, submit."""
import logging
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .config import ROOT

log = logging.getLogger(__name__)

SCREENSHOT_DIR = ROOT / "screenshots"
TRUTHY = {"true", "yes", "y", "1", "on", "checked"}

# Tags every fillable control with data-jh-id so it can be found again when filling.
EXTRACT_FIELDS_JS = """
(prefix) => {
  const skip = new Set(['hidden', 'submit', 'button', 'reset', 'image', 'password', 'file']);
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelOf = el => {
    if (el.labels && el.labels.length) return el.labels[0].innerText;
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const ids = el.getAttribute('aria-labelledby');
    if (ids) {
      const text = ids.split(/\\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' ').trim();
      if (text) return text;
    }
    return el.placeholder || el.name || el.id || '';
  };
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.tagName === 'INPUT' ? (el.type || 'text') : el.tagName).toLowerCase();
    if (skip.has(type) || el.disabled || el.readOnly) continue;
    const choice = type === 'radio' || type === 'checkbox';
    if (!choice && !visible(el)) continue;
    const id = prefix + (i++);
    el.setAttribute('data-jh-id', id);
    const field = {
      id, type,
      label: labelOf(el).trim().slice(0, 200),
      name: el.name || '',
      required: el.required || el.getAttribute('aria-required') === 'true',
    };
    if (type === 'select') field.options = [...el.options].map(o => o.text.trim()).filter(Boolean).slice(0, 60);
    if (choice) {
      field.option = el.value;
      const legend = el.closest('fieldset') && el.closest('fieldset').querySelector('legend');
      if (legend) field.group = legend.innerText.trim().slice(0, 200);
    }
    out.push(field);
  }
  return out;
}
"""


def launch_options(settings) -> dict:
    """BROWSER_PATH (any Chromium-based browser, e.g. Comet) wins over BROWSER_CHANNEL."""
    options = ({"executable_path": settings.browser_path} if settings.browser_path
               else {"channel": settings.browser_channel or None})
    if settings.browser_profile_dir:
        options["user_data_dir"] = str(settings.browser_profile_dir)
    return options


def browser_name(settings) -> str:
    return Path(settings.browser_path).stem if settings.browser_path else settings.browser_channel or "chromium"


def sign_in(settings, url: str, ask=input) -> None:
    """Open the app's browser profile so the person can sign in to sites by hand.
    The logins are saved in the profile and reused by later discover / apply runs."""
    if not settings.browser_profile_dir:
        raise RuntimeError("Set BROWSER_PROFILE_DIR in .env first; that is where logins are kept.")
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    if settings.browser_path:
        # A normal, non-automated window: some sign-in pages refuse automated browsers.
        subprocess.run([settings.browser_path, f"--user-data-dir={settings.browser_profile_dir}",
                        "--no-first-run", url])
    else:
        with open_page(url, **launch_options(settings)):
            ask("Sign in to the sites you need, then press Enter here to close the browser... ")


def _wait_out_reload(page, seconds: float = 4.0) -> None:
    """Comet reloads a page once, a second or two after it opens, which would wipe
    anything already filled in. Give that reload time to come and go."""
    try:
        page.wait_for_event("framenavigated", timeout=seconds * 1000)
        page.wait_for_load_state("load")
    except PlaywrightError:
        pass  # no reload happened


@contextmanager
def open_page(url: str, *, headless: bool = False, **launch):
    """launch is launch_options(settings): which browser to start, and with which profile.
    With user_data_dir the browser keeps its cookies and logins between runs."""
    with sync_playwright() as p:
        try:
            if launch.get("user_data_dir"):
                # A persistent context is closed the same way a browser is.
                browser = p.chromium.launch_persistent_context(headless=headless, **launch)
            else:
                browser = p.chromium.launch(headless=headless, **launch)
        except PlaywrightError as e:
            raise RuntimeError(
                f"Could not start the browser ({launch}). If BROWSER_PROFILE_DIR is set, close any window "
                "already using that profile. Otherwise set BROWSER_CHANNEL to msedge or chrome, point "
                "BROWSER_PATH at a Chromium-based browser, or run: python -m playwright install chromium"
            ) from e
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except PlaywrightError:
                pass  # pages with long-polling never go idle
            if "executable_path" in launch:
                _wait_out_reload(page)
            yield page
        finally:
            browser.close()


def page_text(page) -> str:
    return f"{page.title()}\n\n{page.inner_text('body')}"


def extract_fields(page) -> tuple[list[dict], dict]:
    """Return (fields, index) where index maps field id -> (frame, field).
    Looks inside iframes too, since many applicant tracking systems embed their form in one."""
    fields, index = [], {}
    for n, frame in enumerate(page.frames):
        try:
            found = frame.evaluate(EXTRACT_FIELDS_JS, f"f{n}_")
        except PlaywrightError:
            continue
        for field in found:
            fields.append(field)
            index[field["id"]] = (frame, field)
    return fields, index


def fill_fields(index: dict, fills: list[dict]) -> tuple[int, list[str]]:
    """Apply the planned values. Returns (filled count, problems)."""
    filled, problems = 0, []
    for fill in fills:
        entry = index.get(fill["field_id"])
        if not entry:
            problems.append(f"{fill['field_id']}: not a field on this form")
            continue
        frame, field = entry
        locator = frame.locator(f'[data-jh-id="{field["id"]}"]')
        value = fill["value"]
        try:
            if field["type"] == "select":
                locator.select_option(label=value, timeout=5_000)
            elif field["type"] in ("radio", "checkbox"):
                if value.strip().lower() not in TRUTHY:
                    continue
                locator.check(force=True, timeout=5_000)
            else:
                locator.fill(value, timeout=5_000)
            filled += 1
        except PlaywrightError as e:
            problems.append(f"{field['label'] or field['id']}: {str(e).splitlines()[0]}")
    return filled, problems


def attach_resume(page, resume_file: Path) -> bool:
    """Attach the resume file to the first upload input that asks for a resume/CV."""
    for frame in page.frames:
        for locator in frame.locator('input[type="file"]').all():
            try:
                hint = locator.evaluate(
                    "el => [(el.labels && el.labels[0] ? el.labels[0].innerText : ''), el.name, el.id,"
                    " el.getAttribute('aria-label') || ''].join(' ')"
                )
                if re.search(r"resume|cv|curriculum", hint, re.I):
                    locator.set_input_files(str(resume_file))
                    return True
            except PlaywrightError:
                continue
    return False


def screenshot(page, label: str) -> Path:
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:50] or "application"
    path = SCREENSHOT_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def click_submit(page) -> bool:
    for frame in page.frames:
        candidates = [
            frame.locator('button[type="submit"], input[type="submit"]'),
            frame.get_by_role("button", name=re.compile(r"submit|apply", re.I)),
        ]
        for locator in candidates:
            try:
                if locator.count() and locator.first.is_visible():
                    locator.first.click(timeout=5_000)
                    return True
            except PlaywrightError:
                continue
    return False
