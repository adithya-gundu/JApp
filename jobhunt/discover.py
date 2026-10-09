"""Job discovery: read a listings page, let Claude score each posting, save the good ones."""
import logging

from . import browser
from .llm import extract_jobs

log = logging.getLogger(__name__)


def discover_jobs(tracker, settings, listing_url: str, resume_text: str, *, min_score: int = 60,
                  headless: bool = True, extractor=extract_jobs) -> list[dict]:
    """Returns every posting found, each with "saved" set when it was added to the tracker."""
    with browser.open_page(listing_url, headless=headless, **browser.launch_options(settings)) as page:
        text = browser.page_text(page)

    jobs = extractor(text, listing_url, resume_text)
    known = {(r.get("url") or "").rstrip("/") for r in tracker.list()}
    for job in sorted(jobs, key=lambda j: -j["fit_score"]):
        url = (job["url"] or listing_url).rstrip("/")
        job["saved"] = False
        if job["fit_score"] < min_score or url in known:
            continue
        tracker.create({
            "title": job["title"],
            "company": job["company"],
            "url": url,
            "location": job["location"],
            "tech_stack": job["tech_stack"],
        })
        known.add(url)
        job["saved"] = True
    return jobs
