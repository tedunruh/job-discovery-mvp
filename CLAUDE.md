# Scout (job-discovery-mvp)

Python app: ATS collectors, Neon Postgres, Flask dashboard (`dashboard/`), deployed on Render
with a pip-only build.

## UI work: use Field Guide

All UI is styled through **Field Guide**, the design system in `field-guide/`. Read
`field-guide/README.md` before touching any template or CSS.

Rules:
- Never hard-code a color, font, font size, spacing or radius value in a template or component.
  Use a `var(--fg-…)` semantic token. Layout widths and breakpoints are fine as raw px.
- **The gallery at `/field-guide` is the catalog of approved controls.** Before building a
  screen, pick from it. Reuse `.fg-*` classes from `field-guide/css/components.css` and the
  macros in `dashboard/templates/_fg.html` (`fg.toggle`, `fg.filter`, `fg.card`, `fg.chip`, …); never copy their markup.
- A new component goes into Field Guide first: CSS in `components.css`, a macro in `_fg.html`
  if its markup is more than one class on a native element, and a spec row with every state
  in `dashboard/templates/field_guide.html`. `quality_check.py` fails if a `.fg-` class is
  missing from the gallery.
- Every template includes `{% include "_field_guide_head.html" %}` and uses `<body class="fg-page">`.
- To change a token: edit `field-guide/tokens/*.json`, run `python field-guide/build.py`,
  commit the JSON and `field-guide/dist/tokens.css` together. Never edit `dist/` by hand.
- Fonts are Fraunces (display) and Albert Sans (UI) only. Never add another typeface.
  Albert Sans (UI) text is never lighter than Medium (500); Regular is for Fraunces only.
- Goldenrod, Clay and Sage are never text colors on Parchment, and never the only signal of
  meaning. Pair them with Ink text or an icon.
- Free text (messages, descriptions, body copy) is always left-aligned, even inside a centered
  container. Only short headings and the wordmark may center.
- UI text is sentence case: buttons, links, labels, headings ("Role preferences", not
  "Role Preferences"). Only proper names (Scout, Field Guide, LinkedIn) are capitalized.
- Primary actions and links are Forest. Secondary hierarchy comes from Ink opacity, not new grays.

Design intent lives in Figma file `w7hKbZWJIgb74guqh5uGUF` (Foundations `127:22`,
Color + Text `126:11`). If code and Figma disagree, ask before changing either.

## Before calling UI work done

First run `python scripts/quality_check.py` - it automates the mechanical
standing checks (viewport meta, no overflow, login artwork in bounds, toolbar
alignment, computed-style drift vs. `scripts/quality_baseline.json`) and must
pass. If it flags style drift you intended, accept it with `--update-baseline`.
Add a check there when a new mechanically-checkable cause shows up. Dev setup:
`pip install -r requirements-dev.txt`.

Then read `QUALITY_LOG.md` and run its **Standing checks** - each exists because it
caught a real bug that already shipped once; don't relearn the lesson the hard
way a second time. After finishing a change, log it there: what shipped, how
many corrections it took to get right, and why (a named root cause, not "bug
fixed"). If a rework cause is new and mechanically preventable, add a check for
it so it catches the next one automatically instead of relying on memory.

## Working on Scout (read this first in a new session)

**What it is.** Scout scans public job boards (~8.7k across Greenhouse, Ashby, Lever, Workable,
Workday, Rippling) every 15 minutes, stores roles in **Postgres on Neon (not SQLite)**, shows
them on a Flask dashboard, and sends per-person push alerts through ntfy. Invite-only alpha.
Sprint history, incidents and open items: `docs/SPRINTS.md`. Process lessons: `QUALITY_LOG.md`.

**Where things run**
- **Collector:** GitHub Actions `collect.yml`, dispatched every 15 min by cron-job.org, runs
  `scheduler/run_collectors.py`. Public repo = free minutes. A run takes ~6-10 min; one at a
  time (concurrency group, 25 min timeout).
- **Dashboard:** Render free web service. It sleeps after 15 idle minutes;
  `scripts/keep_awake.sh` (called from the workflow) pings it 6 AM-midnight Central.
- **Other services:** Neon Postgres, Resend (sign-in email), ntfy.sh (alerts), healthchecks.io
  (outage watchdog; the collector pings after each completed run and `/fail` on a crash).
- **Config** is env vars / GitHub secrets, never in the repo: `DATABASE_URL`, `RESEND_API_KEY`,
  `RESEND_FROM`, `SECRET_KEY`, `NTFY_HEALTH_TOPIC`, `HEALTHCHECK_URL`, `ANTHROPIC_API_KEY` (dashboard only: it reads resumes;
  never put it in the collector or GitHub secrets) and optionally `RESUME_MONTHLY_CAP_USD` (default 5), plus `RENDER_API_KEY` locally.
  Locally they live in `.env.local` (git-ignored); `private/` (git-ignored) holds invite material.
  Never print, commit or paste values. The repo is **public**: no tester names, topics or keys.

**Code map**
| Path | What |
|---|---|
| `collectors/` | one module per platform (`fetch(identifier)`), `filters.py` (design title gate), `detect.py` (find a company's board) |
| `scheduler/run_collectors.py` | the run: core companies, then discovered boards in batches; alerts; health |
| `db/db.py`, `db/schema.sql` | all SQL. Schema changes are additive `ALTER ... IF NOT EXISTS` blocks at the end of `schema.sql`; apply them to Neon *before* pushing code that needs them |
| `role_categories.py` | the one matcher for dashboard and alerts (`matches_profile`, `matches_work_type`) |
| `dashboard/` | Flask app, templates (Field Guide design system, see above). `profile.py` is the profile screen; `resume.py` reads resumes (Scout pays: Claude Haiku 5.5, per-person and monthly caps, every call logged in `resume_parses`) |
| `scripts/` | `quality_check.py` (UI checks), `engagement.py`, `actions_usage.py`, `discover_boards.py`, `import_discovered.py`, `add_companies.py`, `try_resume_read.py` (one real resume read: tokens and cost, saves nothing), `convert_profiles.py` |
| `tests/` | `python -m unittest discover tests` (offline) |

**Data model in brief.** `companies.tier`: `core` (original ~140: every role stored), `hot` /
`cold` (discovered boards: design roles only; hot every run, cold in a ~4h rotation), `off`
(kill switch). Everyone sees every company (`user_companies` is unused); a person's role
categories, keywords and saved Work type decide what they see and are alerted about. A board's
**first scan is silent**. `source_health` tracks failures; `scan_marker` is Workday's change
fingerprint (`'rescan'` = silent re-scan).

**Runbook**
- Health: last runs in `scheduler_runs` (should be ~6-10 min, finished); `source_health` statuses;
  `python scripts/engagement.py 7` for per-person use; `python scripts/actions_usage.py`.
- Switch a board/batch off: `UPDATE companies SET tier='off' WHERE ...` (reversible).
- Add boards: `scripts/discover_boards.py` (harvest/validate/enrich/report, read-only) then
  `scripts/import_discovered.py --tier hot --ats <platform>`; import in **batches** and watch two runs.
- Deploy: push to `main` auto-deploys the dashboard; the collector uses `main` at dispatch.
  Check Render's deploy status via its API and `scheduler_runs` for the next run.

**Working agreements (learned the hard way)**
- The user pushes (GitHub Desktop); the assistant does not push. The GitHub CLI is installed at
  `~/.local/bin/gh` (not on PATH; use the full path) and signed in, so the assistant can open PRs
  and read CI and run logs. Run `git status` / `git log` first (the user often commits themselves). Never force-push without explicit steps; a Pull/Sync
  button re-merges rewritten history.
- **Don't build what wasn't asked.** A company-tracking page was built from a decision card and
  removed. Ask or confirm before adding user-facing features. Keep solutions simple.
- Run tests and `scripts/quality_check.py` before committing; gate on the result, not on exit
  codes of a pipeline. Log process lessons in `QUALITY_LOG.md`.
- Dry runs against production: never hold a long transaction (it blocked a real run). Use a
  **Neon branch** for anything long or multi-run.
- Verify, don't assume: check deploys, runs and real data after every change.
- **One chat per sprint, named `Scout — <year> Q<n> Sprint <n>`** (a range for a chat that spans
  several: `Scout — 2026 Q4 Sprints 1–2`). Open it in this repo's folder and file it in the
  sidebar's **Scout** group. At the start of a sprint chat, rename it to match; at the end,
  update `docs/SPRINTS.md` so the next chat can pick up from it.
