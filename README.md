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
- `/stops/`: the next buses and the trip search; `/stops/<id>/` for one stop,
  `/trip/` for a trip
- `/crowding/`: how busy each route is, from riders' reports
- `/reliability/`: riders report a bus that never came or skipped their stop;
  `/reliability/dashboard/` is the prediction accuracy dashboard, for admins
- `/places/`: riders' saved places, Home and favourites
- `/notifications/`: riders turn push notifications on or off for each device,
  and send themselves a test
- `/when-to-leave/`: the next 24 hours as a calendar, with when to leave for
  each event, its steps from Home and a map of the trip; riders can ask the
  trip agent about a trip in the next 2 hours
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

Before the graded A/B run, delete our own test visits on production: open
**Actions → reset the A/B test → Run workflow**, pick `production` and type
`production` to confirm. Left blank, it only counts them. It uses the
production environment's `DATABASE_URL` secret, so nobody copies the
connection string to a laptop, and then checks that production's
`/ecc1032/stats` shows zero impressions. Run it after the last promotion: a
promotion opens `/ecc1032` to check it, and that counts as a visit. See #47.

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
| `fit_calibration` | daily, 10:29 UTC (5:29 AM Eastern) | refits the countdown corrections from the last four weeks of recorded history (#117); until the first fit lands, predictions answer with the raw countdown |
| `score_accuracy` | daily, 10:53 UTC (5:53 AM Eastern) | scores our predictions over the last seven days for the accuracy dashboard (#17), and over the last 31 days on Mondays; the dashboard only shows these saved scores |
| `refresh_calendars` | hourly, at :41 | re-reads each rider's connected calendar address and replaces its events (#25) |
| `notify_trips` | hourly, at :45 | watches riders' trips in the next 2 hours and sends the notifications the AI agent decides on (#29); each run watches until the next one takes over |

- **On time, by the job clock:** GitHub starts scheduled runs hours late, or
  not at all: 8 to 9 hours late in November 2026, when the hourly line went 7
  hours without a run. So `.github/workflows/clock.yml` keeps time instead, the
  way the recorder does. Each clock run stays up about 5 hours, starts a
  `jobs.yml` run in each minute one of its cron lines fires
  (`jobs/clock.py`), then queues the next clock run. GitHub's own late runs are
  the backup: they do nothing while a run the clock started has succeeded in
  the last two hours. To stop the clock, disable the "job clock" workflow in
  the Actions tab and cancel its run.
- **The run log** is at `/admin/` → Scheduled jobs: when each run started and
  ended, what started it (`job clock`, `schedule` for GitHub's own, or a
  person's name), and its summary or error. A failed run also fails its
  workflow run in the Actions tab.
- **Safe to repeat:** runs of the same job never overlap, and every job can run
  late, twice, or after a skipped day without harm. The trip watch is the one
  job meant to outlast its hour: its next run cancels it and takes over.
- **Code and schema stay in step:** the jobs run `main`'s code, and refuse to
  start while the database is missing one of its migrations, as production's is
  until the next promotion.
- **Trip notifications (#29):** `notifications/trips.py`. Each hourly run
  loads the trips starting in the next 2 hours, for riders with notifications
  on and a Home: one visit to the database. Then it checks them every 5
  minutes against the live feed, in memory. Once the time to leave is within
  30 minutes, the agent decides whether and when to notify. A trip gets one
  notification, and another only when its time to leave moves by 5 minutes or
  more. Each one is recorded before it goes out, under a unique constraint, so
  overlapping runs never send it twice: see `/admin/` → Trip notifications.
  Connecting a calendar checks its trips in the next 2 hours right away.
- **Asking the trip agent (#148):** `notifications/ask.py`. On `/when-to-leave/`
  a rider, with or without notifications on, asks the agent about one trip in
  the next 2 hours, and sees whether it would notify them, when, what it would
  say, and what it looked up. The request only saves the question; the agent
  answers in a thread of its own (it can take up to 90 seconds), and the page
  polls for the answer, which is saved for 5 minutes: see `/admin/` → Agent
  answers. The demo office hours are always 25 to 45 minutes away, so graders
  can try it at any hour the shuttles run.
- **By hand:** **Actions → scheduled jobs → Run workflow** runs any job on
  production or staging. Locally, `python manage.py run_job import_history`.
- **Setup:** the workflow reads a `DATABASE_URL` secret from each GitHub
  environment (`production`, `staging`): Neon's direct (unpooled) connection
  string for that site's database. It also passes its own token as
  `GITHUB_TOKEN`, so the history import's GitHub API calls aren't held to the
  60 an hour GitHub allows without one. Set `GITHUB_TOKEN` locally only if you
  hit that limit. The trip watch also needs `OPENROUTER_API_KEY`,
  `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` there, with the same values as the
  site's Render environment: devices subscribed with its public key, so only
  its key pair reaches them. Without them, a run with trips coming up fails
  and says what's missing.

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
