# Sprint 2: production and the shuttle data

**Goal:** put production live with its own database, and build our first story,
the next buses with predicted arrival times
([#5](https://github.com/vchen017/project-yale-shuttle/issues/5)): import the
recorded shuttle history, predict arrivals from it, read the live feed, and
show it all on a next-buses page. Also start the AI feature that tells riders
when to leave.

## What shipped

- [#63](https://github.com/vchen017/project-yale-shuttle/pull/63), closing
  [#34](https://github.com/vchen017/project-yale-shuttle/issues/34):
  production, a second Render service with its own PostgreSQL database on a
  separate Neon branch, so it shares no data or limits with staging. Merging
  still deploys only staging. A "promote to production" workflow deploys a
  commit only if it's on `main` and passed CI, then waits until production's
  new `/healthz` reports that commit and checks that the log-in page, the A/B
  page and its stats load.
- [#59](https://github.com/vchen017/project-yale-shuttle/pull/59), closing
  [#22](https://github.com/vchen017/project-yale-shuttle/issues/22): the
  `history` app and `import_history`, which loads our
  [recorder](https://github.com/vchen017/downtowner-recorder)'s five-minute
  snapshots of the Downtowner feed since Oct 26 into PostgreSQL. Rerunning it
  never duplicates anything, and a missed poll is stored as missing, so a gap
  never reads as "no buses ran".
- [#60](https://github.com/vchen017/project-yale-shuttle/pull/60), for
  [#23](https://github.com/vchen017/project-yale-shuttle/issues/23), stacked on
  #59 and merged with it: `predict_minutes`, our predicted wait for a route's
  next bus at a stop, learned from the history, and `evaluate_predictions`,
  which scores it on held-out days next to the official estimate.
- [#62](https://github.com/vchen017/project-yale-shuttle/pull/62), closing
  [#36](https://github.com/vchen017/project-yale-shuttle/issues/36): the
  `transit` app, our one reader of the live Downtowner feed (routes, stops,
  buses, arrival estimates and announcements), cached for 30 seconds. When the
  feed is down or slow, a page can say live times are unavailable instead of
  crashing.
- [#53](https://github.com/vchen017/project-yale-shuttle/pull/53), closing
  [#42](https://github.com/vchen017/project-yale-shuttle/issues/42) and
  [#52](https://github.com/vchen017/project-yale-shuttle/issues/52):
  `AGENTS.md`, one guide that each of our AI agents reads, plus a pull request
  template in the course's format and issue templates.

- [#66](https://github.com/vchen017/project-yale-shuttle/pull/66), closing
  [#37](https://github.com/vchen017/project-yale-shuttle/issues/37): the start of our AI feature.
  The `assistant` app asks an LLM, through OpenRouter and only from our server,
  whether and when to notify a rider about a trip. It checks the shape of the
  answer and treats a bad answer or an outage as "don't notify". An evaluation
  set of seven made-up trips runs in CI with the LLM mocked.
- [#61](https://github.com/vchen017/project-yale-shuttle/pull/61), closing
  [#57](https://github.com/vchen017/project-yale-shuttle/issues/57): the history import records
  when a route changes its stops, and predictions skip stops a route no longer
  serves.
- [#67](https://github.com/vchen017/project-yale-shuttle/pull/67), closing
  [#38](https://github.com/vchen017/project-yale-shuttle/issues/38): every pull request is now
  linted with Ruff as well as tested, and the README says how to roll back each
  environment. With #63, that finishes our first technical-challenge item, CI
  and CD.
- [#69](https://github.com/vchen017/project-yale-shuttle/pull/69), for
  [#40](https://github.com/vchen017/project-yale-shuttle/issues/40): scheduled jobs. A `jobs`
  app runs them and keeps a run log in `/admin/`, and a GitHub Actions workflow
  starts them. The first job imports the newest shuttle history into
  production every morning.
- [#65](https://github.com/vchen017/project-yale-shuttle/pull/65): a font close to Yale's on
  every page.
- [#70](https://github.com/vchen017/project-yale-shuttle/pull/70), closing
  [#27](https://github.com/vchen017/project-yale-shuttle/issues/27): browser
  push notifications. Riders turn them on or off per device at
  `/notifications/`, see and remove their other devices there, and can send a
  test notification; `/admin/` lists every device and can send a test too.

Still in review:

- [#64](https://github.com/vchen017/project-yale-shuttle/pull/64), extending
  [#23](https://github.com/vchen017/project-yale-shuttle/issues/23), approved: predictions that
  read the live feed first, then the latest recorded polls, then the history,
  and HTTPS-only on both sites. **TODO:** if it merges by Nov 18, move it into
  the list above.

## What works on staging and production

Staging, https://yale-shuttle-staging.onrender.com, deploys from `main`
automatically. Production, https://yale-shuttle.onrender.com, went live on
Nov 14 through the promote workflow. `/healthz` on each shows the commit it
runs. On both:

- the home page, signing up, logging in and out, and `/admin/` for admins
- both grader accounts, which `seed` creates on every deploy
- `/ecc1032` and `/ecc1032/stats`. Production counts its visits in its own
  database, apart from staging's test visits.

The history, predictions and live feed have no page yet; #24 adds one in
Sprint 3. `/admin/` lists the history tables, but they're empty on both
servers so far. Once production runs today's `main`, #69's daily job fills
production's, starting with the last three days. **TODO:** re-check this
section after the last merge and promotion.

## Working with our agents

The countdown rework (#64) showed why every AI-made change needs a price tag.
The agent added a cron to `render.yaml` that would import fresh polls every
five minutes; the review caught that Render bills cron jobs separately, and
that keeping our Neon database awake around the clock would burn about 180 of
the free plan's 100 compute hours a month — enough to suspend both
databases. We cut it and read the live feed through the `transit` helper
instead, and `AGENTS.md` now tells every agent to touch the database hourly
at most.

## Backlog changes for Sprint 3

- Carried over, since we didn't start them: the next-buses page
  ([#24](https://github.com/vchen017/project-yale-shuttle/issues/24)), which
  completes #5, and service alerts on the home screen
  ([#13](https://github.com/vchen017/project-yale-shuttle/issues/13)).
  Predictions (#23) continue in
  [#64](https://github.com/vchen017/project-yale-shuttle/pull/64).
  **TODO:** move #5, #13 and #24 to the Sprint 3 iteration on the board.
- New backlog items from what we found in the recorded data:
  [#54](https://github.com/vchen017/project-yale-shuttle/issues/54) (the real
  stops, not just the official list),
  [#55](https://github.com/vchen017/project-yale-shuttle/issues/55) (where to
  get off during a detour),
  [#57](https://github.com/vchen017/project-yale-shuttle/issues/57) (keep the
  history and predictions right when stops change) and
  [#58](https://github.com/vchen017/project-yale-shuttle/issues/58) (how old a
  bus's location is). We folded
  [#56](https://github.com/vchen017/project-yale-shuttle/issues/56) into #54
  and [#19](https://github.com/vchen017/project-yale-shuttle/issues/19) into
  [#16](https://github.com/vchen017/project-yale-shuttle/issues/16), so each is
  built once.
- Chose our three technical-challenge items, which Sprint 3 needs started:
  [#38](https://github.com/vchen017/project-yale-shuttle/issues/38) CI and CD
  (done: #63 and #67),
  [#39](https://github.com/vchen017/project-yale-shuttle/issues/39) an
  evaluation set for the AI (started in #66) and
  [#40](https://github.com/vchen017/project-yale-shuttle/issues/40) scheduled
  jobs (started in #69). `docs/mgt656/technical.md` lists all three.
- Put our Sprint 3 commitment, our MVP, in the Sprint 3 iteration on the
  board: the AI notification flow
  ([#11](https://github.com/vchen017/project-yale-shuttle/issues/11), through
  [#27](https://github.com/vchen017/project-yale-shuttle/issues/27), shipped in
  [#70](https://github.com/vchen017/project-yale-shuttle/pull/70),
  [#28](https://github.com/vchen017/project-yale-shuttle/issues/28) and
  [#29](https://github.com/vchen017/project-yale-shuttle/issues/29)), calendar
  trip plans ([#8](https://github.com/vchen017/project-yale-shuttle/issues/8),
  through [#25](https://github.com/vchen017/project-yale-shuttle/issues/25) and
  [#26](https://github.com/vchen017/project-yale-shuttle/issues/26)), crowd
  reports ([#12](https://github.com/vchen017/project-yale-shuttle/issues/12)),
  custom alerts
  ([#20](https://github.com/vchen017/project-yale-shuttle/issues/20)), saved
  places ([#7](https://github.com/vchen017/project-yale-shuttle/issues/7)),
  #57, #58, and demo data for graders
  ([#46](https://github.com/vchen017/project-yale-shuttle/issues/46)).
