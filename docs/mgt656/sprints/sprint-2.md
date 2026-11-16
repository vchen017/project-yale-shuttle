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
  where they're starting and going, as an address, a building or a stop
  ([#93](https://github.com/vchen017/project-yale-shuttle/pull/93)).
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

## What works on staging and production

On [staging](https://yale-shuttle-staging.onrender.com) and
[production](https://yale-shuttle.onrender.com): signing up and logging in,
both grader accounts, the home page's alerts, trip search and map, the next
buses, crowding, push notifications, `/admin/`, and `/ecc1032` with its stats.
CI checks every pull request
([#77's run](https://github.com/vchen017/project-yale-shuttle/actions/runs/43921210586)),
and production changes only through the promote workflow
([a run on Nov 16](https://github.com/vchen017/project-yale-shuttle/actions/runs/43965423619)).

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
Sprint 3 commits to our MVP: the AI notification flow
([#11](https://github.com/vchen017/project-yale-shuttle/issues/11)), calendar
trip plans ([#8](https://github.com/vchen017/project-yale-shuttle/issues/8)),
saved places ([#7](https://github.com/vchen017/project-yale-shuttle/issues/7))
and #58, with our technical-challenge items in `docs/mgt656/technical.md`.
