# Yale Shuttle (team cold-flower)

Shuttle arrival predictions for Yale riders, built on the recorded history of
the live Downtowner feeds. MGT 656 team project: Django and PostgreSQL, with
staging on Render and the database on Neon.

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

`render.yaml` defines the staging service. Each deploy runs `build.sh`, which
installs packages, collects static files, migrates the database, and runs
`seed`. Seed is safe to run more than once.

Before the graded A/B run, delete our own test visits with
`python manage.py reset_abtest --yes`.
