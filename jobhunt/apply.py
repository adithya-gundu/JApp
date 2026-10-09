"""Auto-apply: Claude plans the answers, the browser fills the form, you approve the submit."""
import logging

from . import browser
from .llm import plan_form_fill

log = logging.getLogger(__name__)


class ApplyError(RuntimeError):
    pass


def apply_to_job(tracker, settings, job_url: str, resume_text: str, *, page_id: str | None = None,
                 submit: bool = False, headless: bool = False, review: bool = True,
                 planner=plan_form_fill, ask=input, notifier=None) -> dict:
    """Fill the application form at job_url and screenshot it.

    The form is submitted only when submit=True and the person types SUBMIT at the
    prompt after looking at the filled form. The tracker moves to Applied only then.
    """
    record = tracker.get(page_id) if page_id else tracker.find_by_url(job_url)

    with browser.open_page(job_url, headless=headless, **browser.launch_options(settings)) as page:
        title = page.title()
        fields, index = browser.extract_fields(page)
        if not fields:
            raise ApplyError(
                "No form fields found on this page. Open the direct application form URL "
                "(some sites show it only after clicking Apply or signing in)."
            )
        log.info("Found %d form fields; asking Claude for answers.", len(fields))
        plan = planner(fields, resume_text, browser.page_text(page))

        filled, problems = browser.fill_fields(index, plan["fills"])
        attached = bool(settings.resume_file and settings.resume_file.exists()
                        and browser.attach_resume(page, settings.resume_file))
        shot = browser.screenshot(page, title)

        submitted = False
        if submit:
            answer = ask(f"\nFilled {filled} fields. Review the browser window / {shot}.\n"
                         "Type SUBMIT to send this application, anything else to cancel: ")
            if answer.strip() == "SUBMIT":
                if not browser.click_submit(page):
                    raise ApplyError("Could not find a submit button; submit it by hand in the browser.")
                page.wait_for_timeout(3_000)
                browser.screenshot(page, f"{title}-submitted")
                submitted = True
        elif review and not headless:
            ask("\nForm filled, not submitted. Press Enter to close the browser... ")

    if record is None:
        record = tracker.create({"title": title or job_url, "url": job_url})
    unfilled = "; ".join(f"{u['field_id']}: {u['reason']}" for u in plan["unfilled"])
    if submitted:
        tracker.set_status(record["id"], "Applied", follow_up_days=settings.follow_up_days, auto_applied=True)
        tracker.log_email(record["id"], f"Application submitted via LLM+Browser ({filled} fields)")
        if notifier:
            notifier.send(f"Applied: {record.get('title') or title}", job_url)
    else:
        note = f"Draft filled ({filled} fields), not submitted. Screenshot: {shot.name}"
        tracker.log_email(record["id"], note + (f". Needs you: {unfilled}" if unfilled else ""))

    return {
        "record_id": record["id"],
        "fields": len(fields),
        "filled": filled,
        "problems": problems,
        "unfilled": plan["unfilled"],
        "resume_attached": attached,
        "screenshot": shot,
        "submitted": submitted,
    }
