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
- [#64](https://github.com/vchen017/project-yale-shuttle/pull/64), extending
  [#23](https://github.com/vchen017/project-yale-shuttle/issues/23): predictions read
  the live feed first, through the `transit` helper (no imports and no extra
  database wake-ups), then the freshest recorded polls — each implies an
  absolute arrival time, so the countdown ticks instead of jumping — then the
  history, conditioned on how long the stop has been without a bus. Arrivals
  are learned from countdown crossings, not zero-ETAs, because most happen
  between the recorder's five-minute polls. Both sites are now HTTPS-only
  behind Render, with `/healthz` left plain for the health check.
- [#76](https://github.com/vchen017/project-yale-shuttle/pull/76), closing
  [#73](https://github.com/vchen017/project-yale-shuttle/issues/73), from Kyle's Sprint 1
  feedback ([#71](https://github.com/vchen017/project-yale-shuttle/issues/71)): the home page
  opens on a map of the shuttle stops near you. It shows the routes running now
  in their colours and a pin for each stop they serve, and "Show stops near me"
  lists the five closest stops with walking times.
- [#77](https://github.com/vchen017/project-yale-shuttle/pull/77), closing
  [#75](https://github.com/vchen017/project-yale-shuttle/issues/75), also from #71: tests of the
  main rider workflows, end to end, and of a slow, dead or broken feed. CI now
  applies every migration to an empty PostgreSQL database, next to its check
  that every model change has a migration. [#77's checks](https://github.com/vchen017/project-yale-shuttle/actions/runs/43921210586) passed:
  Ruff, both migration checks, and the tests against PostgreSQL.
- [#78](https://github.com/vchen017/project-yale-shuttle/pull/78), closing
  [#74](https://github.com/vchen017/project-yale-shuttle/issues/74), also from #71: a report on
  our shuttle data, below.
- [#72](https://github.com/vchen017/project-yale-shuttle/pull/72): the Yale typeface, Yale 2024,
  on every page, with #65's Newsreader as the fallback.
- [#83](https://github.com/vchen017/project-yale-shuttle/pull/83), closing
  [#24](https://github.com/vchen017/project-yale-shuttle/issues/24): the next-buses page, for
  story [#5](https://github.com/vchen017/project-yale-shuttle/issues/5). A rider picks a stop, and
  optionally where they're going, and sees each running route's next buses,
  with the official time next to our prediction. When the feed is down, the
  page says so instead of failing. Every stop on the home page's map links
  to it. [#88](https://github.com/vchen017/project-yale-shuttle/pull/88) then
  settled a question from #83's review: while the feed had a bus, the
  prediction column repeated the official time. It now always shows our own
  prediction, from the recorded history.
- [#82](https://github.com/vchen017/project-yale-shuttle/pull/82), closing
  [#13](https://github.com/vchen017/project-yale-shuttle/issues/13): service alerts on the home
  page. It shows the Downtowner's announcements and the alerts our admins post
  in `/admin/`, newest first. The admins' alerts show at once, and the feed's
  are added after the page loads, so it never waits on the feed.
- [#93](https://github.com/vchen017/project-yale-shuttle/pull/93), closing
  [#92](https://github.com/vchen017/project-yale-shuttle/issues/92), a story we
  added this sprint: riders type where they're starting and where they're
  going, as an address, a Yale building or a stop, instead of picking from
  lists of every stop. The trip page says which stop to walk to, which shuttle
  to take and where to get off, with the next buses there. Tapping a stop on
  the map fills either box.
- [#80](https://github.com/vchen017/project-yale-shuttle/pull/80) and
  [#81](https://github.com/vchen017/project-yale-shuttle/pull/81): the map's tiles fixed for
  production, then the map redrawn with vector tiles, so it's sharp on any
  screen, with bolder routes. The home page leads with the hook again.
- [#84](https://github.com/vchen017/project-yale-shuttle/pull/84), closing
  [#20](https://github.com/vchen017/project-yale-shuttle/issues/20): admins
  manage the alerts they post. `/admin/` shows whether each one is scheduled,
  showing or ended, "End now" takes one down early, and times are in Eastern
  time.
- [#90](https://github.com/vchen017/project-yale-shuttle/pull/90): the home
  page's service alerts take one slim line each, with the full text a tap
  away, so they no longer push the map down.
- [#89](https://github.com/vchen017/project-yale-shuttle/pull/89), closing
  [#46](https://github.com/vchen017/project-yale-shuttle/issues/46): `seed`
  adds demo data for graders, starting with a welcome alert, and each app can
  add its own.
- [#91](https://github.com/vchen017/project-yale-shuttle/pull/91), closing
  [#12](https://github.com/vchen017/project-yale-shuttle/issues/12): riders
  report how busy their bus is at `/crowding/`, from empty to full.

## What works on staging and production

Staging, https://yale-shuttle-staging.onrender.com, deploys from `main`
automatically. Production, https://yale-shuttle.onrender.com, went live on
Nov 14 through the promote workflow. `/healthz` on each shows the commit it
runs. On both:

- the home page, signing up, logging in and out, and `/admin/` for admins
- both grader accounts, which `seed` creates on every deploy
- `/ecc1032` and `/ecc1032/stats`. Production counts its visits in its own
  database, apart from staging's test visits.

On both sites, the home page shows any service alerts and the map of stops, and `/notifications/`
turns push notifications on for a device and sends a test. Both redirect plain
HTTP to HTTPS. Production changes only through the promote workflow, which
deploys a commit that passed CI and waits until production's `/healthz`
reports it. For example, [this run on Nov 16](https://github.com/vchen017/project-yale-shuttle/actions/runs/43925739876) promoted `26718d4`.

Production's history has held Nov 14–16 since
[this import on Nov 16](https://github.com/vchen017/project-yale-shuttle/actions/runs/43941907407),
and #69's daily import adds each new day at 4:17 AM Eastern. The next-buses
page (#83) shows the predictions it feeds.

## Our data

Kyle asked where our data has gaps and how far to trust the arrivals we infer.
[`docs/mgt656/data-quality.md`](../data-quality.md) answers for Nov 9–15, and
`python manage.py data_quality` reruns it for any week:

- **The recording has been complete since Nov 6.** The recorder missed 480
  polls in all, nearly all on Oct 27–30, when its runs couldn't save.
- **Bus GPS is mostly fresh, except on Blue West,** where 7% of positions were
  over 10 minutes old.
  [#58](https://github.com/vchen017/project-yale-shuttle/issues/58) will tell
  riders when a position is old.
- **The arrivals we infer agree with the buses' own GPS.** For 87% of them, the
  inferred time was within 2 minutes of when the GPS says the bus reached the
  stop; when we're off, it's usually early.

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

- Added from Kyle's Sprint 1 feedback
  ([#71](https://github.com/vchen017/project-yale-shuttle/issues/71)) and done
  this sprint: the stops map (#73), the rider-workflow and feed tests (#75),
  and the data report (#74).
- Pulled forward from our Sprint 3 commitment and done this sprint: crowd
  reports (#12), custom alerts (#20) and demo data for graders (#46).
- New backlog items from what we found in the recorded data:
  [#54](https://github.com/vchen017/project-yale-shuttle/issues/54) (the real
  stops, not just the official list),
  [#55](https://github.com/vchen017/project-yale-shuttle/issues/55) (where to
  get off during a detour) and
  [#58](https://github.com/vchen017/project-yale-shuttle/issues/58) (how old a
  bus's location is). #57 shipped with #61. We folded
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
  #58, and demo data for graders
  ([#46](https://github.com/vchen017/project-yale-shuttle/issues/46)).
