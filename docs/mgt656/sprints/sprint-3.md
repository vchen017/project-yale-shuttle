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
  running route has a bus that was on its official path in the last hour and
  then stayed more than 200 m off it for three sightings in a row spanning
  at least 10 minutes, the next-buses page marks the route "possible
  detour" and the home page's alerts say to ask the driver where to get
  off — the question Austin's detoured bus couldn't answer on Nov 12. The
  flag stays up for as long as the bus stays off its path. Admins can name
  the stops a detour skips on any alert, and a sample detour alert joins
  the demo seed. Detection samples the live feed into a small table (one
  row at most every 2 minutes, an hour kept), so a detour survives a
  restart or a Render sleep — the home page's first paint never waits on
  the feed. Deliberately conservative: a bus flags its route only after the
  feed showed it on the official path (a garage departure never was), a
  sighting it misses restarts its run, the off-run must span those 10
  minutes before the badge shows, and a nightly job reads the recorded
  history and refuses to flag a route in the hours it is routinely off its
  drawn path — Pink's 5 AM garage runs, Purple's evening loops and
  Blue-Night's 6 PM and midnight hours, measured over Nov 8–20 — while a
  real detour is one day, not half the window. Vivian's replay of Nov
  9–20 at the page's real two-minute pace then caught 35 flags the
  fraction rule alone couldn't refuse — one bus's loop never moves 15%
  of a route's fixes — so the job now learns, per route and hour, three
  shapes on three of the last 14 days: the whole route off its drawn
  path, one bus making detour-sized runs off it alone, and the walk back
  to the garage at the day's end. The same replay now shows 22 episodes
  (Nov 10–18: 30 → 19): each recurring shape's first two occurrences
  before the nightly job learns it, two plausible minor Purple detours,
  one four-minute bus that left the feed — and Green's real Nov 12
  detour still flags. Vivian's rerun of that replay on the latest
  commit then caught the job learning from the wrong hours: on a
  noon-to-noon service day a route that runs through midday never saw
  its just-after-midnight walk home as the day's end (Purple's next
  late-morning fix was the day's last), and a run counted only in the
  hour it started, while detection flags in the hour the run grows old
  in. The service day now runs 4 AM to 4 AM Eastern — the recorded
  history has no fixes at all between 3 and 5 AM — and a run counts in
  every hour it covers: the replay is down to 16 episodes, Green still
  flags, and the job's tests pass whatever the date. In review, fourth
  round addressed.
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
  running green on it. The Nov 19 audit turned into four fix pull
  requests, three merged the same evening:
  [#136](https://github.com/vchen017/project-yale-shuttle/pull/136)
  (history fixes: an index for the nightly scoring window, a corrections
  cache that clears when a new fit lands, a per-query lookup, a rate-limit
  race), [#137](https://github.com/vchen017/project-yale-shuttle/pull/137)
  (the crowding demo no longer deletes moderated samples), and
  [#138](https://github.com/vchen017/project-yale-shuttle/pull/138) (the
  grader usernames reserved at signup, so nobody can take
  `admin@example.com` or `rider@example.com`);
  [#135](https://github.com/vchen017/project-yale-shuttle/pull/135) (parse
  guards for malformed feed answers — per review, a junk answer now 503s
  like a down feed instead of showing empty pages) is approved and
  awaiting merge. Two more
  of Austin's merged the same night: the notification evaluation set grew
  to 22 trips, each tested against 15 kinds of malformed answer and 5
  kinds of outage with the model mocked
  ([#141](https://github.com/vchen017/project-yale-shuttle/pull/141)), and
  AGENTS.md now asks every author to check their change against real data
  and say what they saw
  ([#140](https://github.com/vchen017/project-yale-shuttle/pull/140)) —
  extended the next day to every push, with the check repeatable and
  paced to what the code really runs
  ([#144](https://github.com/vchen017/project-yale-shuttle/pull/144)).
  The nightly jobs' weekly and monthly scoring — the runs that used to
  risk the job's 45-minute timeout — scored 20,069 and 91,945 queries
  in 3.8 and 14.8 seconds on Nov 23.

## What works on staging and production

Everything from sprint 2: signing up and logging in, both grader accounts,
the home page's alerts, trip search and map, the next buses with our
predictions, crowding, missed-bus reports, saved places, push
notifications, `/admin/` with the accuracy dashboard, and `/ecc1032` with
its stats. All three calendar pull requests are merged
([#125](https://github.com/vchen017/project-yale-shuttle/pull/125) directly,
#127 into the stack, #126 last, closing #26 and #8), and
[staging](https://yale-shuttle-staging.onrender.com) is serving the whole
calendar MVP — verified at the merge commit, and again at `3c2a3bb` on
Nov 19 with the evening's audit fixes aboard (home, login, signup and a
stop page all answering). Production was promoted on Nov 20 (serving
`3c2a3bb`) and has the calendar MVP too — the whole sprint is on both
sites, checked via `/healthz` and page answers on each. Before
submitting (#44) we check both grader accounts on both
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
that aren't route-shaped, and on first paint read only the warm detection
cache and let the page's swap-in do the real work (#129). Its second
round's tests all passed while the samples still lived in the in-memory
cache; replaying the recorded polls — the check #139 just made a rule —
caught what the mocks couldn't: JSON turns numbers' keys into strings, so
the samples came back with `"1"` where the code looked up route `1`, and
detection would have silently done nothing in production. Two lines fixed
it and a test pins the round-trip (#129).

The third round came from Vivian running the recorded polls through the
real detection at the pace riders actually hit the page — a load every
two minutes — where our own replay had stepped the polls at their
recorded five-minute pace and found nothing to fix. At page pace the
usual loops and garage walks flagged 35 times in twelve days; the fix is
her suggested one, a nightly job that learns each route's hours from
three shapes instead of one, and the lesson is now a rule: match the
pace the code really runs (#129, #144). Her harness also caught the
job's own tests dying on a fixed date the moment Nov 10 rolled out of
its 14-day window — CI would have gone red for every pull request on
Nov 24.

Her next rerun — on the commit that fix produced — found two more,
both in what the nightly job learns, not in detection itself: the
service day the job grouped fixes into ran noon to noon, which hides a
midday-running route's walk home, and a run counted in its starting
hour while detection flags in the next one. The review arrived with
the patch and the replay numbers to prove it (22 → 16 episodes); the
premise re-counted from the database (no fixes at all between 3 and
5 AM Eastern, so the 4 AM boundary sits in dead time), and a rerun of
the replay on the patched commit confirmed hers one-for-one before
pushing (#129). The whole loop ran on the reviewer's own numbers.

A full-codebase audit by two read-only agents on Nov 19 surfaced eight
real bugs across the apps — a malformed-but-parseable feed answer was a
500 on four pages sharing one cache, a stale corrections cache left raw
countdowns after a new fit landed, the crowding demo wiped moderated
samples, and the grader usernames were free for anyone to claim at
signup — and four small fix pull requests went up the same evening
(#135–#138). The same audit cleared the rest: network mocking, time
zones, and the migration graph all checked clean.

Two more from the trip page, both caught before they shipped: in #131
the AI sentence-cased its headline with `.capitalize()`, which quietly
lowercased the route name inside it — the template tests caught it, and
the fix was to let the data's own capitalization through. In #133 its
first mock-up showed a walking time it had made up, contradicting the
live data next to it; the walking-is-quicker notice now quotes the
data's own arrival time instead of a number nobody measured.

## Backlog changes for Sprint 4

Opening state, to grow as the sprint goes: the sprint 4 report
([#45](https://github.com/vchen017/project-yale-shuttle/issues/45)).
`technical.md`'s per-item detail
([#41](https://github.com/vchen017/project-yale-shuttle/issues/41))
merged as
[#143](https://github.com/vchen017/project-yale-shuttle/pull/143).
Making the long waits better
([#118](https://github.com/vchen017/project-yale-shuttle/issues/118)) was
measured in this sprint's last days: on held-out days nothing beat the
shipped predictor, so it closed as measured, no code change. Choosing
where to get off during a detour
(#55) moved up into this sprint — its pull request is in review above.
Merged as the sprint closes: the notifications setup checklist
([#145](https://github.com/vchen017/project-yale-shuttle/pull/145), for
[#11](https://github.com/vchen017/project-yale-shuttle/issues/11)) and
the A/B reset button for the graded run
([#147](https://github.com/vchen017/project-yale-shuttle/pull/147), for
[#47](https://github.com/vchen017/project-yale-shuttle/issues/47)).
Approved and waiting on a final review and a merge: the job that
watches riders' trips and sends the agent's notifications
([#142](https://github.com/vchen017/project-yale-shuttle/pull/142), for
[#29](https://github.com/vchen017/project-yale-shuttle/issues/29)) and
the shareable live-trip link
([#146](https://github.com/vchen017/project-yale-shuttle/pull/146), for
[#10](https://github.com/vchen017/project-yale-shuttle/issues/10) — its
new sharing app's first migration rides the next production promote).
