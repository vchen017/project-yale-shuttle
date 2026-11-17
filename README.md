# Yale Shuttle

[![ci](https://github.com/vchen017/project-yale-shuttle/actions/workflows/ci.yml/badge.svg)](https://github.com/vchen017/project-yale-shuttle/actions/workflows/ci.yml)

**Staging:** https://yale-shuttle-staging.onrender.com ·
**Board:** [Yale Shuttle project](https://github.com/users/vchen017/projects/1)

Shuttle arrival predictions for Yale riders, built on the recorded history of
the live Downtowner feeds, which
[downtowner-recorder](https://github.com/vchen017/downtowner-recorder) saves
every 5 minutes. MGT 656 team project: Django and PostgreSQL, with staging on
Render and the database on Neon.

**Working on it?** Read [AGENTS.md](AGENTS.md) first: where code goes, our
rules, and what "done" means. Our AI agents read it too.

## Run it locally

You need Python 3.12; [uv](https://docs.astral.sh/uv/) installs it for you.

```sh
uv venv && uv pip install -r requirements.txt
export DJANGO_DEBUG=1   # local only: SQLite and a throwaway secret key
.venv/bin/python manage.py migrate
SEED_ADMIN_PASSWORD=pick-one SEED_RIDER_PASSWORD=pick-another .venv/bin/python manage.py seed
.venv/bin/python manage.py runserver
```

On Windows without uv: `python -m venv .venv`, then use
`.venv\Scripts\python.exe` in place of `.venv/bin/python` everywhere.

Run the tests with `DJANGO_DEBUG=1 .venv/bin/python manage.py test`, and the
linter with `uvx ruff check --fix .` (on Windows without uv:
`uv pip install -r requirements-dev.txt` once, then
`.venv\Scripts\ruff.exe check --fix .`). CI runs both on every pull request,
the tests against PostgreSQL, and GitHub won't merge until they pass.

## Configuration

Everything comes from environment variables; nothing secret goes in git.

| Variable | What it is |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL (required unless `DJANGO_DEBUG=1`) |
| `SECRET_KEY` | Django's secret key (required unless `DJANGO_DEBUG=1`) |
| `DJANGO_DEBUG` | `1` for local development only |
| `ALLOWED_HOSTS` | extra host names, comma-separated; Render's own is added automatically |
| `OPENROUTER_API_KEY` | key for the AI feature's LLM calls (OpenRouter); without it the assistant never notifies |
| `OPENROUTER_MODEL` | OpenRouter model name; defaults to `openai/gpt-4o-mini` |
| `SEED_ADMIN_PASSWORD`, `SEED_RIDER_PASSWORD` | grader account passwords, used by `manage.py seed` |
| `HISTORY_RECORDER_REPO` | where to import history from; defaults to `vchen017/downtowner-recorder` |

## Pages

- `/`: home; `/signup/`, `/login/`; `/admin/` for admins
- `/notifications/`: riders turn push notifications on or off for each device,
  and send themselves a test
- `/ecc1032`: the A/B test page, and `/ecc1032/stats` for its live results
  (`ecc1032` is the start of the SHA-1 hash of our team nickname, cold-flower)

## How the predictions work

`history/predictions.py` answers in two layers, live first:

- **Live:** first the Downtowner feed itself, through the transit app's
  `etas()` helper (cached 30 seconds, so no per-request cost and no extra
  database wake-ups); when the feed is down or has nothing for the route,
  the freshest recorded polls take over. Each poll implies an absolute
  arrival time (`polled_at + eta_minutes`), and that answer ages a minute
  per minute between polls — a countdown that ticks, unlike the official
  app's — moving only sooner, and expiring 3 minutes after coming due, so a
  listing that never arrives can't hold the answer hostage.
- **History:** the fallback when nothing live is fresh. Arrivals are
  detected by countdown crossing (an ETA at or below 5 extrapolated to
  zero — the recorder polls every 5 minutes, so most arrivals land between
  polls), and learned as walk-up medians per (day type, half-hour bucket),
  then conditioned on how long the stop has been without a bus.

Score it against the official ETA on days held out from training:

```sh
DJANGO_DEBUG=1 python manage.py evaluate_predictions
```

This takes a couple of minutes and needs imported history. Numbers from
before November 14, 2026 aren't comparable to later ones: the ground truth
changed from "polls with eta 0" (which misses most arrivals) to crossing
detection, which is also what the model trains on now.

## Deploying

`render.yaml` defines both environments, each with its own Neon database:

- **Staging** (`yale-shuttle-staging`) deploys every merge to `main`. Its
  database is the Neon project's `main` branch.
- **Production** (`yale-shuttle`) never deploys on merge. To release, open
  **Actions → promote to production → Run workflow**. It checks that the commit
  is on `main` and passed CI, deploys exactly that commit, then waits until
  production's `/healthz` reports it and the log-in and A/B pages answer. Its
  database is `yale_shuttle` on the Neon branch `production`.

Every deploy runs `build.sh`, which installs packages, collects static files,
migrates the database, and runs `seed` (safe to run more than once). Render
only switches to a new deploy once `/healthz` answers, and `/healthz` shows
which commit each environment is running.

Production's one-time setup, after `render.yaml` creates the service:

1. On the `yale-shuttle` service's Environment page in Render, set
   `DATABASE_URL` (Neon: branch `production`, database `yale_shuttle`, pooled
   connection string), `SEED_ADMIN_PASSWORD` and `SEED_RIDER_PASSWORD`.
2. In the GitHub repo's Actions settings, add its deploy hook (Render: the
   service's Settings, Deploy Hook) as the secret
   `RENDER_PRODUCTION_DEPLOY_HOOK`, and its address as the variable
   `PRODUCTION_URL`.

### When a deploy goes wrong

A broken deploy shouldn't take a site down:

- If `build.sh` fails (a package won't install, or a migration errors), Render
  stops and keeps serving the previous deploy. On PostgreSQL each Django
  migration runs in a transaction, so a failed one leaves the database as it
  was.
- If the new version builds but can't answer `/healthz`, Render never switches
  traffic to it.

To roll back a deploy that went live but misbehaves:

- **Production:** open **Actions → promote to production → Run workflow** and
  enter the last good commit, which is what production's `/healthz` showed
  before. It redeploys exactly that commit and checks it, like any promotion.
  In a hurry, the deploy list on the `yale-shuttle` service's Events page in
  Render has a Rollback button that skips the rebuild.
- **Staging:** revert the pull request on GitHub (its **Revert** button opens a
  new pull request) and merge that. Staging redeploys from `main`, so a
  rollback in Render would be undone by the next merge.

Rolling back code doesn't undo migrations, so keep them safe for the previous
version to run against: add tables and nullable columns freely, and remove or
rename things in a later pull request, once nothing reads them.

Before the graded A/B run, delete our own test visits on production with
`python manage.py reset_abtest --yes`, run from a laptop with production's
`DATABASE_URL` (Render's free plan has no shell). See #47.

## Push notifications

Riders turn them on at `/notifications/`, once on each device. The browser
gives us a subscription for that device, which `notifications.push.send()`
uses to send it a notification through the browser's push service. A device
whose subscription has lapsed is removed the next time we send to it.

- **iPhone and iPad** only allow push for a site added to the Home Screen
  (Share → Add to Home Screen), opened from there. The page tells riders so.
- **Setup, once per site:** run `python manage.py vapid_keys` and set the two
  lines it prints on the site's Environment page in Render. Each site gets its
  own pair; changing a site's keys signs every device out of notifications.
  Until they're set, the page says notifications aren't set up.
- **Testing by hand:** "Send a test notification" on `/notifications/`, or the
  action of that name on the devices in `/admin/`.

## Scheduled jobs

`.github/workflows/jobs.yml` runs the app's scheduled jobs on GitHub's machines,
against production's database (free for a public repo). Each app declares its
jobs in its own `jobs.py`, and `python manage.py run_job --list` shows them all.

| Job | When | What it does |
|---|---|---|
| `import_history` | daily, 9:17 UTC (4:17 AM Eastern) | loads the recorder's last three days of shuttle history (#22), plus up to five older days from the last four weeks that aren't loaded yet |
| `score_accuracy` | daily, 10:53 UTC (5:53 AM Eastern) | scores our predictions over the last seven days for the accuracy dashboard (#17), and over the last 31 days on Mondays; the dashboard only shows these saved scores |

- **The run log** is at `/admin/` → Scheduled jobs: when each run started and
  ended, what started it, and its summary or error. A failed run also fails its
  workflow run in the Actions tab.
- **Safe to repeat:** runs of the same job never overlap, and every job can run
  late, twice, or after a skipped day without harm. GitHub sometimes starts a
  scheduled run late, or drops one when it's busy.
- **Code and schema stay in step:** the jobs run `main`'s code, and refuse to
  start while the database is missing one of its migrations, as production's is
  until the next promotion.
- **By hand:** **Actions → scheduled jobs → Run workflow** runs any job on
  production or staging. Locally, `python manage.py run_job import_history`.
- **Setup:** the workflow reads a `DATABASE_URL` secret from each GitHub
  environment (`production`, `staging`): Neon's direct (unpooled) connection
  string for that site's database. It also passes its own token as
  `GITHUB_TOKEN`, so the history import's GitHub API calls aren't held to the
  60 an hour GitHub allows without one. Set `GITHUB_TOKEN` locally only if you
  hit that limit.

Mind Neon's free plan, which staging and production share:

- **100 compute hours a month.** The database stays awake for 5 minutes after
  each use, so a job that touched it every 5 minutes would keep it awake around
  the clock, use the hours up, and suspend both databases. Database jobs run
  hourly at most.
- **1 GB of storage.** Each day of shuttle history adds about 10 MB to the
  database it's imported into (measured on Nov 15, 2026), so the schedule
  imports into production only. Past 1 GB, Neon blocks writes until space is
  freed.

## Neon

The staging database is the `yale-shuttle` project on Neon (AWS US East, Ohio).
Ask Vivian for an invite to the Neon organization, then:

- Run `neon link` once in this folder. It records the project in `.neon` and
  pulls the connection strings into `.env`; both files are git-ignored. Django
  doesn't read `.env`, so local development keeps using SQLite unless you
  export `DATABASE_URL`.
- Claude Code loads Neon's agent skills from `.claude/skills/` and the Neon MCP
  server from `.mcp.json`, which holds no key: you sign in to Neon the first
  time it's used.
- `neon skills status` checks the skills for updates.
