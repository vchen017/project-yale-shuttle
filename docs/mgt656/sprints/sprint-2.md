# Sprint 2: production and the shuttle data

**Goal:** put production live with its own database, and ship our first story,
the next buses with our predicted arrival times
([#5](https://github.com/vchen017/project-yale-shuttle/issues/5)), learned from
our recorded shuttle history. Also start the AI feature that tells riders when
to leave.

## What shipped

- **Production** on its own Neon database, deployed only by a promote workflow
  that checks the commit passed CI
  ([#63](https://github.com/vchen017/project-yale-shuttle/pull/63)), with CI
  linting every pull request
  ([#67](https://github.com/vchen017/project-yale-shuttle/pull/67)) and a daily
  job importing new history
  ([#69](https://github.com/vchen017/project-yale-shuttle/pull/69)).
- **The next buses**
  ([#83](https://github.com/vchen017/project-yale-shuttle/pull/83)): each
  route's official time next to our prediction, learned from the imported
  history ([#59](https://github.com/vchen017/project-yale-shuttle/pull/59),
  [#60](https://github.com/vchen017/project-yale-shuttle/pull/60),
  [#88](https://github.com/vchen017/project-yale-shuttle/pull/88)). Riders type
  where they're starting and going, as an address, a stop or a Yale building
  by its everyday name ([#93](https://github.com/vchen017/project-yale-shuttle/pull/93),
  [#101](https://github.com/vchen017/project-yale-shuttle/pull/101)).
  On the last evening the prediction learned to lead with the live
  countdown, corrected by the recorded history, and the official time is
  scored the way a rider with the app open sees it: on the recorded
  history our prediction is 12.8 minutes off on average against 13.1 for
  the official countdown itself, down from 15.4 under the old rule
  ([#119](https://github.com/vchen017/project-yale-shuttle/pull/119),
  [#120](https://github.com/vchen017/project-yale-shuttle/pull/120)),
  with the corrections refitted nightly against the database.
- **From Kyle's feedback**
  ([#71](https://github.com/vchen017/project-yale-shuttle/issues/71)): a map of
  the stops near you ([#76](https://github.com/vchen017/project-yale-shuttle/pull/76)),
  tests of the rider workflows and a down feed
  ([#77](https://github.com/vchen017/project-yale-shuttle/pull/77)), and
  [a report on our data](../data-quality.md)
  ([#78](https://github.com/vchen017/project-yale-shuttle/pull/78)): no gaps
  since Nov 6, and 87% of our inferred arrivals are within 2 minutes of the
  buses' own GPS.
- **Also:** service alerts
  ([#82](https://github.com/vchen017/project-yale-shuttle/pull/82),
  [#84](https://github.com/vchen017/project-yale-shuttle/pull/84)), crowd
  reports ([#91](https://github.com/vchen017/project-yale-shuttle/pull/91)),
  push notifications
  ([#70](https://github.com/vchen017/project-yale-shuttle/pull/70)), the AI
  feature's first piece with an evaluation set in CI
  ([#66](https://github.com/vchen017/project-yale-shuttle/pull/66)), and demo
  data for graders ([#89](https://github.com/vchen017/project-yale-shuttle/pull/89)).
- **Ahead of plan:** missed-bus reports
  ([#102](https://github.com/vchen017/project-yale-shuttle/pull/102)), an admin
  accuracy dashboard scored nightly
  ([#103](https://github.com/vchen017/project-yale-shuttle/pull/103)), saved places
  with Home first ([#104](https://github.com/vchen017/project-yale-shuttle/pull/104)),
  crowd-report moderation, limits and blocking
  ([#112](https://github.com/vchen017/project-yale-shuttle/pull/112), [#114](https://github.com/vchen017/project-yale-shuttle/pull/114)),
  crowding by route and hour for admins ([#113](https://github.com/vchen017/project-yale-shuttle/pull/113)),
  how old a bus's location is ([#108](https://github.com/vchen017/project-yale-shuttle/pull/108)), the
  trip-notification agent's tools ([#115](https://github.com/vchen017/project-yale-shuttle/pull/115)),
  and a cleaner nav and home page
  ([#107](https://github.com/vchen017/project-yale-shuttle/pull/107), [#111](https://github.com/vchen017/project-yale-shuttle/pull/111)).

## What works on staging and production

On [staging](https://yale-shuttle-staging.onrender.com) and
[production](https://yale-shuttle.onrender.com): signing up and logging in,
both grader accounts, the home page's alerts, trip search and map, the next
buses, crowding, missed-bus reports, saved places, push notifications,
`/admin/` with the accuracy dashboard, and `/ecc1032` with its stats.
CI checks every pull request
([#77's run](https://github.com/vchen017/project-yale-shuttle/actions/runs/43921210586)),
and production changes only through the promote workflow
([a run on Nov 17](https://github.com/vchen017/project-yale-shuttle/actions/runs/44204364380)).

## Working with our agents

The address search worked when our agent tested it locally, but not on
staging: Photon, the free address search behind it, refuses requests from
Render's servers.
Checking staging right after the merge, as `AGENTS.md` asks, caught it within
the hour. We moved the lookups into riders' browsers
([#95](https://github.com/vchen017/project-yale-shuttle/pull/95)), and
`AGENTS.md` now says why.

## Backlog changes for Sprint 3

Kyle's three items are done. Our data added the real stop locations
([#54](https://github.com/vchen017/project-yale-shuttle/issues/54)), detours
([#55](https://github.com/vchen017/project-yale-shuttle/issues/55)) and old bus
positions ([#58](https://github.com/vchen017/project-yale-shuttle/issues/58)).
Eight later items are done early
([#6](https://github.com/vchen017/project-yale-shuttle/issues/6), [#7](https://github.com/vchen017/project-yale-shuttle/issues/7), [#16](https://github.com/vchen017/project-yale-shuttle/issues/16), [#17](https://github.com/vchen017/project-yale-shuttle/issues/17),
[#18](https://github.com/vchen017/project-yale-shuttle/issues/18), [#19](https://github.com/vchen017/project-yale-shuttle/issues/19), [#28](https://github.com/vchen017/project-yale-shuttle/issues/28), [#58](https://github.com/vchen017/project-yale-shuttle/issues/58)),
and an audit of how we score the official times added
[#116](https://github.com/vchen017/project-yale-shuttle/issues/116) to [#118](https://github.com/vchen017/project-yale-shuttle/issues/118),
two of which shipped this sprint (#116, #117).
Sprint 3 commits to our MVP: the rest of the AI notification flow
([#11](https://github.com/vchen017/project-yale-shuttle/issues/11)) and calendar
trip plans ([#8](https://github.com/vchen017/project-yale-shuttle/issues/8)), with our
technical-challenge items in `docs/mgt656/technical.md`.
