# Job Hunt Command Center

Local implementation of the automation stack described on the Notion "Job Hunt Command Center" page.

| Layer | Module | What it does |
|---|---|---|
| Job discovery | `jobhunt/discover.py` | Opens a listings page, Claude extracts postings and scores fit against your resume, good matches are saved |
| Form filling | `jobhunt/apply.py` | Opens an application form, Claude plans the answers from your resume, the browser fills them and takes a screenshot |
| Tracking | `jobhunt/tracker.py` | Updates status, dates and the Email Log in your Notion database (or a local JSON file) |
| Email monitoring | `jobhunt/email_watcher.py` | Reads unread mail over IMAP, matches it to an application, updates the status |
| Notifications | `jobhunt/notify.py` | Webhook and/or email alert on every status change |

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env      # then fill in .env
```

1. **Claude**: put your API key in `ANTHROPIC_API_KEY`.
2. **Notion**: create an internal integration at <https://www.notion.so/profile/integrations>, copy its
   secret into `NOTION_TOKEN`, open the Job Applications database, choose `...` > Connections and add the
   integration, then copy the 32-character ID from the database URL into `NOTION_DB_ID`.
   Until both are set the app tracks applications in `data/applications.json`.
3. **Resume**: save it as plain text in `resume.txt`. Add an "Application answers" section for what a
   resume does not say (work authorization, salary range, notice period); anything Claude cannot answer
   from that file is left blank and reported back to you.
4. **Browser**: Edge by default (`BROWSER_CHANNEL=msedge`). To use another Chromium-based browser such
   as Comet, set `BROWSER_PATH` to its `.exe`. Set `BROWSER_PROFILE_DIR` to give the app its own browser
   profile that keeps logins between runs, then sign in once with `python -m jobhunt login [url]` (it
   opens a normal window on that profile; sign in, close it). This is a separate profile from your
   everyday one, because Chromium refuses automation on its default profile folder. The app cannot call
   the browser's built-in assistant.
5. **Email** (optional): `IMAP_HOST`, `EMAIL_ADDRESS`, and an app password in `EMAIL_PASSWORD`.

Check everything with:

```powershell
.\.venv\Scripts\python.exe -m jobhunt check
```

## Commands

```powershell
$jh = ".\.venv\Scripts\python.exe"
& $jh -m jobhunt list                                   # pipeline
& $jh -m jobhunt add --title "Backend Engineer" --company Acme --url https://...
& $jh -m jobhunt discover https://company.com/careers --min-score 60
& $jh -m jobhunt apply https://company.com/jobs/123/apply          # fill + screenshot only
& $jh -m jobhunt apply https://company.com/jobs/123/apply --submit # fill, then you type SUBMIT
& $jh -m jobhunt watch-email                            # one pass over unread mail
& $jh -m jobhunt watch-email --loop 30                  # keep checking every 30 minutes
& $jh -m jobhunt followups                              # applied roles past their follow-up date
& $jh -m jobhunt status <id> "Final Round"
& $jh -m jobhunt daily                                  # email watcher + follow-ups + pipeline
```

`python apply.py <url>` and `python email_watcher.py` work too, as named on the Notion page.

To run the watcher every 30 minutes without keeping a terminal open (the Windows equivalent of the cron
line on the Notion page):

```powershell
schtasks /Create /TN "JobHuntEmailWatcher" /SC MINUTE /MO 30 /TR "D:\JApp\.venv\Scripts\python.exe D:\JApp\email_watcher.py"
```

## Behaviour worth knowing

- **Nothing is submitted without you.** `apply` fills the form and stops. With `--submit` it shows you the
  filled form and submits only if you type `SUBMIT`. The tracker moves to Applied only after a real submit.
- **Password and file fields are never filled by Claude.** A resume upload is attached only if you set
  `RESUME_FILE`. Forms behind a login need you to sign in; point `apply` at the form's direct URL.
- **Statuses only move forward.** A "next steps" email will not pull a Final Round application back to
  Phone Screen. Rejections always apply. Date Applied is set once, when the status becomes Applied.
- **Your inbox is left as it is.** Mail is read without being marked as read; handled messages are
  remembered in `data/processed_emails.json`.
- **Notion schema is read at run time**, so Status can be a status or a select property, Company can be
  text or select, and so on. Properties missing from the database are skipped with a warning.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The apply tests drive a real browser against `tests/fixtures/job_form.html` with Claude stubbed out.
