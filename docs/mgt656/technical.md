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
- **Pull requests:** [#63](https://github.com/vchen017/project-yale-shuttle/pull/63) (production's own database and the promote
  step, for [#34](https://github.com/vchen017/project-yale-shuttle/issues/34)) and [#67](https://github.com/vchen017/project-yale-shuttle/pull/67) (Ruff on every pull request, and how
  to roll back, for [#38](https://github.com/vchen017/project-yale-shuttle/issues/38)).
- **See it working:** any pull request's Checks tab; the Actions tab's
  "promote to production" runs; and `/healthz` on
  [staging](https://yale-shuttle-staging.onrender.com/healthz) and
  [production](https://yale-shuttle.onrender.com/healthz), which show the
  commit each one runs.

### 2. An evaluation set for the AI (AI)

A fixed set of trips with the decisions we expect from the notification LLM,
run like tests: in CI with the LLM mocked, and by hand against the real model
([#39](https://github.com/vchen017/project-yale-shuttle/issues/39), growing
the set from [#37](https://github.com/vchen017/project-yale-shuttle/issues/37)).

- **The set:** `assistant/evals.py` holds 22 example trips, each with the
  decision we expect and the answer a good model would give. They cover buses
  on time, buses running late, tight trips, routes with no prediction, and the
  live feed being down (with our prediction, and with nothing at all). Each
  case is graded as notify, stay quiet, or "either is fine" for judgment calls.
  Whatever the model says, any notification must come before the rider has to
  leave for the stop, and no notification is allowed when there is no arrival
  time to time it by.
- **In CI (LLM mocked):** `assistant/test_evalset.py`, `tests.py` and
  `test_agent.py` check that the set is big enough and covers every situation,
  that every case is turned into the request we mean to send (including
  "the feed is unavailable" and "no prediction" in words, never blank), that
  each case's good answer passes through both the one-shot decision and the
  tool-using agent, and that fifteen kinds of malformed answer and five kinds
  of failure (a timeout, no route, an HTTP 500, a rate limit, a dropped
  connection) become "don't notify" for every case without crashing. No test
  reaches the network.
- **Against the real model:** `OPENROUTER_API_KEY=... python manage.py
  evaluate_assistant` runs the same cases through OpenRouter and prints
  PASS/FAIL per case and how many decisions match (`--agent` runs them through
  the tool-using agent instead). An unreachable model is reported as an error,
  not scored as a decision, so an outage can't look like a result.
- **Pull requests:** [#66](https://github.com/vchen017/project-yale-shuttle/pull/66) (the one-shot decision, [#37](https://github.com/vchen017/project-yale-shuttle/issues/37)),
  [#115](https://github.com/vchen017/project-yale-shuttle/pull/115) (the tool-using agent, [#28](https://github.com/vchen017/project-yale-shuttle/issues/28)) and [#141](https://github.com/vchen017/project-yale-shuttle/pull/141) (the
  22-case set and its tests, [#39](https://github.com/vchen017/project-yale-shuttle/issues/39)).
- **See it working:** any pull request's Checks tab runs the set with the LLM
  mocked; `evaluate_assistant` runs it against the real model.
- **Latest score:** not yet run against the real model. The key isn't in
  anyone's local environment yet; the first run (and each run after a prompt or
  model change) gets recorded here with its date, the model
  (`OPENROUTER_MODEL`) and the score.

### 3. Scheduled jobs (Backend)

The jobs that keep the app current without anyone clicking: trip
notifications, calendar refreshes, and loading new shuttle history, each run
recorded and safe to repeat ([#40](https://github.com/vchen017/project-yale-shuttle/issues/40)).

| Job | When (Eastern) | What it does |
|---|---|---|
| `import_history` | daily, 4:17 AM | loads the recorder's latest shuttle history ([#22](https://github.com/vchen017/project-yale-shuttle/issues/22)) |
| `fit_calibration` | daily, 5:29 AM | refits how far off the feed's countdowns run, which our predictions correct for |
| `score_accuracy` | daily, 5:53 AM | scores our predictions for the accuracy dashboard ([#17](https://github.com/vchen017/project-yale-shuttle/issues/17)) |
| `refresh_calendars` | hourly, at :41 | re-reads riders' connected calendars ([#25](https://github.com/vchen017/project-yale-shuttle/issues/25)) |
| `notify_trips` | hourly, at :45 | watches trips in the next 2 hours and sends the AI agent's notifications ([#29](https://github.com/vchen017/project-yale-shuttle/issues/29), in review in [#142](https://github.com/vchen017/project-yale-shuttle/pull/142)) |

- **Where they run:** on GitHub's machines, against production's database:
  `.github/workflows/jobs.yml`, free for a public repo. GitHub starts
  scheduled runs hours late or not at all: 8 to 9 hours late in November 2026.
  So the job clock (`.github/workflows/clock.yml`, `jobs/clock.py`) keeps time
  instead. Each clock run stays up about 5 hours, starts each cron line's run
  in its minute, then queues the next clock run. GitHub's own schedule stays as
  the backup, and steps aside while the clock is working.
- **Handling failure:**
  - Every run is recorded with its start, end, trigger and summary or
    traceback. A failed job also fails its workflow run in the Actions tab.
  - Runs of the same job never overlap: a unique constraint on unfinished runs
    makes a second one step aside.
  - Every job is safe to run late, twice, or after a skipped run.
  - Jobs refuse to run new code against a database that's missing one of its
    migrations.
  - To keep within Neon's free plan, they touch the database hourly at most.
- **Main files:**
  - `jobs/registry.py` (the `@job` decorator)
  - `jobs/runner.py` and `jobs/models.py` (running and recording)
  - `jobs/management/commands/run_job.py`
  - `jobs/clock.py` and each app's `jobs.py`
  - the two workflows
- **Pull requests:**
  - [#69](https://github.com/vchen017/project-yale-shuttle/pull/69): the runner, run log and daily import
  - [#124](https://github.com/vchen017/project-yale-shuttle/pull/124): scoring in a few bulk reads
  - [#125](https://github.com/vchen017/project-yale-shuttle/pull/125): the calendar refresh
  - [#130](https://github.com/vchen017/project-yale-shuttle/pull/130): the job clock
  - [#142](https://github.com/vchen017/project-yale-shuttle/pull/142): trip notifications, in review
- **See it working:** `/admin/` → Scheduled jobs lists every run, and "job
  clock" under what started it marks an on-time run. Also the Actions tab's
  "scheduled jobs" and "job clock" runs, and `python manage.py run_job --list`.

## Testing strategy

- Django `TestCase` tests for each app, run with `python manage.py test`. CI runs
  them against PostgreSQL on every pull request.
- The A/B test page is tested over HTTP the way a browser uses it: each visitor
  keeps their variant, one impression per visitor, at most one click, the
  CSRF-protected form, the 50/50 split, and `preferred` chosen by click rate.
- The seed command is tested for being safe to rerun, for refusing to run
  without passwords, for creating each app's demo data (#46) without undoing an
  admin's changes, and for never adding A/B test visits.
- The main rider workflows run end to end through Django's test client
  (`core/test_rider_workflows.py`): signing up and landing on the stops map,
  turning notifications on and sending a test, logging out, logging back in,
  and a wrong password.
- When the shuttle feed is slow, down or answers garbage, every page still
  loads, because none waits on the feed; the map's data answers 503 and the
  map says live data is unavailable. Requests time out after 5 seconds.
  Answers are reused for 30 seconds at most, so once the feed goes down,
  riders never see old times as if they were live. Stale bus positions get
  their own warning in
  [#58](https://github.com/vchen017/project-yale-shuttle/issues/58).
- Every outside service is mocked: the Downtowner feed
  (`transit.downtowner.urlopen`), OpenRouter (`assistant.openrouter.urlopen`),
  push services (`notifications.push.webpush`), riders' calendar addresses
  (`calendars.ical.urlopen`) and the recorder's GitHub reads
  (`history/sources.py`). Running the whole suite with outbound connections
  blocked finds none. The prediction evaluation (`evaluate_predictions`)
  runs entirely on imported history.
- CI, on every pull request: Ruff; a check that every model change has a
  migration; every migration applied to an empty PostgreSQL database; then all
  the tests against PostgreSQL 17.
- Scheduled jobs: the runner, registry and `run_job` are tested against the
  database. A test checks that every job's cron line is in `jobs.yml` and that
  every line starts a job. The job clock runs on a fake clock: each line in its
  minute, the hand-over to the next clock run, and never past its time limit.
- Real recorded data, not only mocks (AGENTS.md, #139): changes to
  predictions, detours and scoring are checked by replaying the recorder's
  history. For example, the accuracy scoring was measured on 20,145 real
  queries before and after #124.
- Not tested automatically:
  - The browser JavaScript (the map, the notification switch) and real push
    delivery. We check those by hand, in a phone-sized and a desktop-sized
    window, before each submission.
  - The real model's answers. They cost money and vary from run to run, so
    `evaluate_assistant` runs them by hand.
  - The workflows themselves (CI, promotion, the jobs and the clock). They
    only run on GitHub, so we watch their first runs in the Actions tab after
    each change.
