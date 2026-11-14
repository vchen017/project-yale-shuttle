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

## Pages

- `/`: home; `/signup/`, `/login/`; `/admin/` for admins
- `/ecc1032`: the A/B test page, and `/ecc1032/stats` for its live results
  (`ecc1032` is the start of the SHA-1 hash of our team nickname, cold-flower)

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

Before the graded A/B run, delete our own test visits on production with
`python manage.py reset_abtest --yes`, run from a laptop with production's
`DATABASE_URL` (Render's free plan has no shell). See #47.

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
