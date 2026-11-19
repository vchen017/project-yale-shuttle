# Sprint 3: the calendar MVP

**Goal:** our MVP: a rider connects their calendar by its ICS address
([#25](https://github.com/vchen017/project-yale-shuttle/issues/25)), the
site plans each class and event as a trip
([#26](https://github.com/vchen017/project-yale-shuttle/issues/26)), and
the when-to-leave page says when to leave for every one of them
([#8](https://github.com/vchen017/project-yale-shuttle/issues/8)) — beside
the rest of the AI notification flow
([#11](https://github.com/vchen017/project-yale-shuttle/issues/11)).

## What shipped

- **Connect a calendar by its ICS address**
  ([#125](https://github.com/vchen017/project-yale-shuttle/pull/125)): a
  rider pastes their calendar's ICS address on `/calendars/`; we fetch and
  parse it on the server, an hourly job re-reads each address and replaces
  its events, and disconnecting removes the calendar and its events.
- **The trip plan for an event**
  ([#126](https://github.com/vchen017/project-yale-shuttle/pull/126)): for
  each event at a Yale building, the route to ride, the stop to walk to,
  where to get off, and when to leave. Close enough says walk. Vivian's
  review fixes: matching no longer mistakes a location's city or state for
  a building, the planner only boards a bus the walk can actually catch,
  and estimates plan on the routes running at the event's hour — a 9 PM
  lecture checked in the afternoon now rides the night route — from the
  recorded polls, falling back to the routes running now. The demo events
  sit at fixed New York times so the page stays filled between deploys.
- **Know when to leave**
  ([#127](https://github.com/vchen017/project-yale-shuttle/pull/127)):
  `/when-to-leave/` lists the next 24 hours' events ordered by when to
  leave, not when they start. Within 45 minutes of an event it shows the
  live board; further out, an estimate, labelled as one. Events we can't
  plan say why: no Home set yet, the feed being down, no route running
  (with the walk's minutes as the news), or a location we can't match. The
  feed being down never breaks the page.
- **Demo data:** every seed run gives the rider account a demo calendar
  with three upcoming events, plus a demo Home if they have none,
  re-anchored to today so the page shows real plans on every deploy.
- **Choosing where to get off during a detour**
  ([#129](https://github.com/vchen017/project-yale-shuttle/pull/129), for
  [#55](https://github.com/vchen017/project-yale-shuttle/issues/55)): when a
  running route's buses have been more than 200 m off its official path for
  three polls in a row (about 15 minutes), the next-buses page marks the
  route "possible detour" and the home page's alerts say to ask the driver
  where to get off — the question Austin's detoured bus couldn't answer on
  Nov 12. Admins can name the stops a detour skips on any alert, and a
  sample detour alert joins the demo seed. Detection samples the live feed
  into a one-minute cache — the home page's first paint never waits on the
  feed — and is deliberately conservative: only a bus the feed shows on the
  route's path flags it (garage departures never do), one stray bus is
  never enough, and a poll with none of the route's buses breaks the
  streak. In review.
- **Scheduled jobs start on time**
  ([#130](https://github.com/vchen017/project-yale-shuttle/pull/130)): a
  job clock fires each job in its due minute even when GitHub's runner
  wakes up late, and the run log shows what ran and when (#40).
- **The trip page leads with one answer**
  ([#132](https://github.com/vchen017/project-yale-shuttle/pull/132),
  [#134](https://github.com/vchen017/project-yale-shuttle/pull/134)): one
  sentence on what to do now, a walking-is-quicker notice when it is, and
  the steps and board folded away below (#131, #133).
- **Also:** the accuracy dashboard scores a date range with a few bulk
  reads instead of a query per scored query
  ([#124](https://github.com/vchen017/project-yale-shuttle/pull/124),
  Vivian's follow-up to #17, merged 2026-11-18), and the nightly jobs are
  running green on it. Three audit-fix pull requests are in review
  ([#135](https://github.com/vchen017/project-yale-shuttle/pull/135),
  [#136](https://github.com/vchen017/project-yale-shuttle/pull/136),
  [#137](https://github.com/vchen017/project-yale-shuttle/pull/137)):
  parse guards for malformed feed answers, history fixes (an index for the
  nightly scoring window, a corrections cache that clears when a new fit
  lands, a rate-limit race), and the crowding demo no longer deleting
  moderated samples.

## What works on staging and production

Everything from sprint 2: signing up and logging in, both grader accounts,
the home page's alerts, trip search and map, the next buses with our
predictions, crowding, missed-bus reports, saved places, push
notifications, `/admin/` with the accuracy dashboard, and `/ecc1032` with
its stats. #125 is merged, and [staging](https://yale-shuttle-staging.onrender.com)
is serving it along with the trip page; #127 is merged into the stack, and
#126 is the one pull request left — approved and green — carrying the trip
planner and the when-to-leave page to
main, and [production](https://yale-shuttle.onrender.com) gets all of it
at the next promote. Before submitting (#44) we check both grader accounts on both
sites, the AI feature on staging, and every page in phone-sized and
desktop-sized windows.

## Working with our agents

The when-to-leave page's first template read a plan field through the
wrong object (`plan.plan.walk_all_the_way`, which is None on any row that
has no trip plan — every event no route could reach), so those events
rendered as if unwalkable. The fix was to stop reaching through two
objects: the planner now carries the minutes the page needs on the
`EventPlan` the template actually receives (#127).

The detour feature's first draft had two bugs its own test run caught
before anything shipped: it crashed when the feed answered with entries
that weren't routes at all, and it computed detection during the home
page's first paint, which would have made the page wait on the feed —
exactly what #13's design forbids. Both fixes were small: ignore entries
that aren't route-shaped, and on first paint read only the one-minute
detection cache and let the page's swap-in do the real work (#129).

A full-codebase audit by two read-only agents on Nov 19 surfaced eight
real bugs across three apps — a malformed-but-parseable feed answer was a
500 on four pages sharing one cache, a stale corrections cache left raw
countdowns after a new fit landed, the crowding demo wiped moderated
samples — and three small fix pull requests went up the same evening
(#135, #136, #137). The same audit cleared the rest: network mocking,
time zones, and the migration graph all checked clean.

## Backlog changes for Sprint 4

Opening state, to grow as the sprint goes: finishing `technical.md`'s
per-item detail
([#41](https://github.com/vchen017/project-yale-shuttle/issues/41)) and
the sprint 4 report
([#45](https://github.com/vchen017/project-yale-shuttle/issues/45)).
Making the long waits better
([#118](https://github.com/vchen017/project-yale-shuttle/issues/118)) was
measured in this sprint's last days: on held-out days nothing beat the
shipped predictor, so the proposal is to close it as measured, no code
change, if the team agrees. Choosing where to get off during a detour
(#55) moved up into this sprint — its pull request is in review above.
