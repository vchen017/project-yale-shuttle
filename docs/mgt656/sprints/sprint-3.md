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
  a building, and the planner only boards a bus the walk can actually
  catch.
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

## What works on staging and production

Everything from sprint 2: signing up and logging in, both grader accounts,
the home page's alerts, trip search and map, the next buses with our
predictions, crowding, missed-bus reports, saved places, push
notifications, `/admin/` with the accuracy dashboard, and `/ecc1032` with
its stats. The calendar stack above is reviewed and green; it reaches
[staging](https://yale-shuttle-staging.onrender.com) as each pull request
merges, and [production](https://yale-shuttle.onrender.com) at the next
promote. Before submitting (#44) we check both grader accounts on both
sites, the AI feature on staging, and every page in phone-sized and
desktop-sized windows.

## Working with our agents

The when-to-leave page's first template read a plan field through the
wrong object (`plan.plan.walk_all_the_way`, which is None on any row that
has no trip plan — every event no route could reach), so those events
rendered as if unwalkable. The fix was to stop reaching through two
objects: the planner now carries the minutes the page needs on the
`EventPlan` the template actually receives (#127).

## Backlog changes for Sprint 4

Opening state, to grow as the sprint goes: helping riders choose where to
get off during a detour
([#55](https://github.com/vchen017/project-yale-shuttle/issues/55)),
making the long waits better
([#118](https://github.com/vchen017/project-yale-shuttle/issues/118)),
finishing `technical.md`'s per-item detail
([#41](https://github.com/vchen017/project-yale-shuttle/issues/41)), and
the sprint 4 report
([#45](https://github.com/vchen017/project-yale-shuttle/issues/45)).
