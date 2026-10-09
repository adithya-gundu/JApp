"""Command line: python -m jobhunt <command>"""
import argparse
import logging
import os
import sys
import time
from collections import Counter

from .config import PROPERTY_MAP, STATUS_FLOW, TERMINAL_STATUSES, load_settings
from .notify import Notifier
from .tracker import get_tracker


def _resume(settings) -> str:
    if not settings.resume_path.exists():
        raise RuntimeError(f"Resume not found at {settings.resume_path}. Save your resume there as plain text.")
    return settings.resume_path.read_text(encoding="utf-8")


def _row(record: dict) -> str:
    return (f"  {record.get('status') or '-':<20} {(record.get('company') or '-')[:22]:<22} "
            f"{(record.get('title') or '-')[:40]:<40} {record['id']}")


def cmd_check(args, settings) -> int:
    ok = True
    print(f"Tracker backend : {settings.backend}")
    if settings.backend == "notion":
        try:
            schema = get_tracker(settings).schema
            missing = [name for key, name in PROPERTY_MAP.items() if name not in schema and key != "title"]
            print(f"Notion database : reachable, {len(schema)} properties")
            if missing:
                print(f"  missing properties (those fields are skipped): {', '.join(missing)}")
        except Exception as e:  # report any Notion failure rather than a traceback
            ok = False
            print(f"Notion database : FAILED - {e}")
    else:
        print(f"Local database  : {settings.local_db_path}")

    has_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    print(f"Claude API key  : {'set' if has_key else 'NOT SET (needed for discover / apply)'}")
    print(f"Claude model    : {settings.model}")
    print(f"Resume          : {settings.resume_path} ({'found' if settings.resume_path.exists() else 'MISSING'})")
    email_ready = bool(settings.imap_host and settings.email_address and settings.email_password)
    print(f"Email watcher   : {'configured' if email_ready else 'not configured'}")
    notify = [n for n, v in (("webhook", settings.webhook_url), ("email", settings.smtp_host and settings.notify_to)) if v]
    print(f"Notifications   : {', '.join(notify) or 'console only'}")
    try:
        from . import browser
        with browser.open_page("about:blank", headless=True, **browser.launch_options(settings)):
            pass
        profile = f", profile {settings.browser_profile_dir}" if settings.browser_profile_dir else ", clean profile"
        print(f"Browser         : {browser.browser_name(settings)} launches{profile}")
    except Exception as e:
        ok = False
        print(f"Browser         : FAILED - {e}")
    return 0 if ok else 1


def cmd_list(args, settings) -> int:
    records = get_tracker(settings).list()
    order = {status: i for i, status in enumerate(STATUS_FLOW + TERMINAL_STATUSES)}
    counts = Counter(r.get("status") or "-" for r in records)
    print(f"{len(records)} applications: " + ", ".join(f"{s} {n}" for s, n in counts.items()))
    for record in sorted(records, key=lambda r: order.get(r.get("status"), 99)):
        print(_row(record))
    return 0


def cmd_add(args, settings) -> int:
    record = get_tracker(settings).create({"title": args.title, "company": args.company, "url": args.url})
    print(f"Saved {args.title} at {args.company} ({record['id']})")
    return 0


def cmd_status(args, settings) -> int:
    tracker = get_tracker(settings)
    record = tracker.set_status(args.id, args.status, follow_up_days=settings.follow_up_days)
    Notifier(settings).send(f"{record.get('company')}: {record.get('title')} -> {args.status}")
    return 0


def cmd_discover(args, settings) -> int:
    from .discover import discover_jobs
    tracker, resume = get_tracker(settings), _resume(settings)
    for url in args.urls:
        jobs = discover_jobs(tracker, settings, url, resume, min_score=args.min_score)
        print(f"{url}: {len(jobs)} postings")
        for job in sorted(jobs, key=lambda j: -j["fit_score"]):
            flag = "saved" if job["saved"] else "     "
            print(f"  [{job['fit_score']:>3}] {flag}  {job['title']} at {job['company']} - {job['rationale']}")
    return 0


def cmd_apply(args, settings) -> int:
    from .apply import apply_to_job
    result = apply_to_job(
        get_tracker(settings), settings, args.url, _resume(settings),
        page_id=args.page_id, submit=args.submit, headless=args.headless,
        review=sys.stdin.isatty(), notifier=Notifier(settings),
    )
    print(f"Filled {result['filled']} of {result['fields']} fields. Screenshot: {result['screenshot']}")
    for item in result["unfilled"]:
        print(f"  needs you: {item['field_id']} - {item['reason']}")
    for problem in result["problems"]:
        print(f"  problem: {problem}")
    print("Submitted; tracker moved to Applied." if result["submitted"] else "Not submitted.")
    return 0


def cmd_login(args, settings) -> int:
    from . import browser
    print(f"Opening {browser.browser_name(settings)} with the app's profile ({settings.browser_profile_dir}).")
    print("Sign in to the sites you need, then close the browser window.")
    browser.sign_in(settings, args.url)
    print("Logins saved. discover and apply will reuse them.")
    return 0


def cmd_watch_email(args, settings) -> int:
    from .email_watcher import watch_emails
    tracker, notifier = get_tracker(settings), Notifier(settings)
    while True:
        results = watch_emails(tracker, settings, notifier)
        print(f"{len(results)} application emails found")
        for r in results:
            change = f" -> {r['status']}" if r["status"] else ""
            print(f"  {r['record'].get('company')}: {r['subject']}{change}")
        if not args.loop:
            return 0
        time.sleep(args.loop * 60)


def cmd_followups(args, settings) -> int:
    due = get_tracker(settings).due_followups()
    print(f"{len(due)} applications due for a follow-up")
    for record in due:
        print(_row(record) + f"  (follow up since {record['follow_up'][:10]})")
    return 0


def cmd_daily(args, settings) -> int:
    """The daily rhythm from the Command Center: email watcher, follow-ups, pipeline."""
    if settings.imap_host:
        args.loop = 0
        cmd_watch_email(args, settings)
    else:
        print("Email watcher not configured; skipping.")
    cmd_followups(args, settings)
    return cmd_list(args, settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobhunt", description="Job Hunt Command Center")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify configuration and connections").set_defaults(func=cmd_check)
    sub.add_parser("list", help="show the pipeline").set_defaults(func=cmd_list)
    sub.add_parser("followups", help="applied roles whose follow-up date has passed").set_defaults(func=cmd_followups)
    sub.add_parser("daily", help="email watcher + follow-ups + pipeline").set_defaults(func=cmd_daily)

    p = sub.add_parser("add", help="save a job to the tracker")
    p.add_argument("--title", required=True)
    p.add_argument("--company", required=True)
    p.add_argument("--url", default="")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("status", help="set an application's status")
    p.add_argument("id")
    p.add_argument("status", choices=STATUS_FLOW + TERMINAL_STATUSES)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("discover", help="scrape listing pages, score fit, save good matches")
    p.add_argument("urls", nargs="+")
    p.add_argument("--min-score", type=int, default=60)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("apply", help="fill a job application form (submits only with --submit + your confirmation)")
    p.add_argument("url")
    p.add_argument("--page-id", help="tracker record to update (default: matched by URL)")
    p.add_argument("--submit", action="store_true", help="offer to submit after you review the filled form")
    p.add_argument("--headless", action="store_true")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("login", help="open the app's browser profile so you can sign in to job sites")
    p.add_argument("url", nargs="?", default="https://www.perplexity.ai/")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("watch-email", help="check unread mail for replies and update statuses")
    p.add_argument("--loop", type=int, default=0, metavar="MINUTES", help="keep running, checking every N minutes")
    p.set_defaults(func=cmd_watch_email)
    return parser


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpx2", "notion_client"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, load_settings())
    except (RuntimeError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
