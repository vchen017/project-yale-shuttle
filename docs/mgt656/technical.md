# Technical notes

## Technical-challenge items

### 1. CI and CD (Delivery)

Every change is checked before it merges, staging deploys itself, and
production changes only when we decide to promote a commit that staging has
already run.

- **CI:** every pull request and push to `main` runs Ruff (lint) and the test
  suite against PostgreSQL 17, and checks that the migrations match the models.
  Branch protection on `main` requires that job and one approval, admins
  included. Files: `.github/workflows/ci.yml`, `ruff.toml`.
- **CD to staging:** Render redeploys `yale-shuttle-staging` on every merge to
  `main`. Files: `render.yaml`, `build.sh`.
- **Promotion to production:** the "promote to production" workflow deploys
  one commit that is on `main` and passed CI, through Render's deploy hook, then
  waits until production's `/healthz` reports that commit and checks the log-in
  and A/B pages. Production never deploys on merge. Files:
  `.github/workflows/promote.yml`, `core/views.py` (`healthz`).
- **Safe failures and rollback:** a failed build or migration leaves the
  previous deploy serving; a version that can't answer `/healthz` never goes
  live; rolling back means promoting the previous good commit. See "When a
  deploy goes wrong" in the README.
- **Pull requests:** [#34](https://github.com/vchen017/project-yale-shuttle/issues/34)
  (production and promotion), [#38](https://github.com/vchen017/project-yale-shuttle/issues/38)
  (lint and rollback).
- **See it working:** any pull request's Checks tab; the Actions tab's
  "promote to production" runs; and `/healthz` on
  [staging](https://yale-shuttle-staging.onrender.com/healthz) and
  [production](https://yale-shuttle.onrender.com/healthz), which show the
  commit each one runs.

### 2. An evaluation set for the AI (AI)

A fixed set of trips with the decisions we expect from the notification LLM,
run like tests: in CI with the LLM mocked, and by hand against the real model.
Details to come with [#39](https://github.com/vchen017/project-yale-shuttle/issues/39).

### 3. Scheduled jobs (Backend)

The jobs that keep the app current without anyone clicking: trip
notifications, calendar refreshes, and loading new shuttle history, each run
recorded and safe to repeat. Details to come with
[#40](https://github.com/vchen017/project-yale-shuttle/issues/40).

## Testing strategy

- Django `TestCase` tests for each app, run with `python manage.py test`. CI runs
  them against PostgreSQL on every pull request.
- The A/B test page is tested over HTTP the way a browser uses it: each visitor
  keeps their variant, one impression per visitor, at most one click, the
  CSRF-protected form, the 50/50 split, and `preferred` chosen by click rate.
- The seed command is tested for being safe to rerun and for refusing to run
  without passwords.
- The recorder's GitHub reads (`history/sources.py`) are the only network code,
  and every test mocks them. The prediction evaluation
  (`evaluate_predictions`) runs entirely on imported history.
