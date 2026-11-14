# Yale Shuttle

[![ci](https://github.com/vchen017/project-yale-shuttle/actions/workflows/ci.yml/badge.svg)](https://github.com/vchen017/project-yale-shuttle/actions/workflows/ci.yml)

**Staging:** https://yale-shuttle-staging.onrender.com ·
**Board:** [Yale Shuttle project](https://github.com/users/vchen017/projects/1)

Shuttle arrival predictions for Yale riders, built on the recorded history of
the live Downtowner feeds, which
[downtowner-recorder](https://github.com/vchen017/downtowner-recorder) saves
every 5 minutes. MGT 656 team project: Django and PostgreSQL, with staging on
Render and the database on Neon.

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

Run the tests with `DJANGO_DEBUG=1 .venv/bin/python manage.py test`. CI runs
them against PostgreSQL on every pull request.

## Configuration

Everything comes from environment variables; nothing secret goes in git.

| Variable | What it is |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL (required unless `DJANGO_DEBUG=1`) |
| `SECRET_KEY` | Django's secret key (required unless `DJANGO_DEBUG=1`) |
| `DJANGO_DEBUG` | `1` for local development only |
| `ALLOWED_HOSTS` | extra host names, comma-separated; Render's own is added automatically |
| `SEED_ADMIN_PASSWORD`, `SEED_RIDER_PASSWORD` | grader account passwords, used by `manage.py seed` |
| `HISTORY_RECORDER_REPO` | where to import history from; defaults to `vchen017/downtowner-recorder` |

## Pages

- `/`: home; `/signup/`, `/login/`; `/admin/` for admins
- `/ecc1032`: the A/B test page, and `/ecc1032/stats` for its live results
  (`ecc1032` is the start of the SHA-1 hash of our team nickname, cold-flower)

## How the predictions work

`history/predictions.py` answers in two layers, live first:

- **Live:** the freshest recorded polls each imply an absolute arrival time
  (`polled_at + eta_minutes`). The earliest one still ahead is the next bus,
  and the answer ages a minute per minute between polls — a countdown that
  ticks, unlike the official app's. Estimates only ever move sooner; ones
  that come due expire after 3 minutes, so a listing that never arrives
  can't hold the answer hostage.
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

`render.yaml` defines the staging service, plus a cron job that imports the
recorder's freshest polls every five minutes — the fresh data our live
predictions read. The cron job bills separately from the free web service,
so it is only created when the blueprint is applied with it enabled. Each
deploy runs `build.sh`, which installs packages, collects static files,
migrates the database, and runs `seed`. Seed is safe to run more than once.

Before the graded A/B run, delete our own test visits with
`python manage.py reset_abtest --yes`.

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
