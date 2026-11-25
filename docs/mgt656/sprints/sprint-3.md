# Sprint 3: the calendar MVP

**Goal:** our MVP. A rider connects their calendar by its ICS address
([#25](https://github.com/vchen017/project-yale-shuttle/issues/25)), the site
plans each class and event as a trip
([#26](https://github.com/vchen017/project-yale-shuttle/issues/26)), and the
when-to-leave page says when to leave for each one
([#8](https://github.com/vchen017/project-yale-shuttle/issues/8)), alongside
the rest of the AI notification flow
([#11](https://github.com/vchen017/project-yale-shuttle/issues/11)).

## What shipped

- **The calendar MVP:** connecting a calendar
  ([#125](https://github.com/vchen017/project-yale-shuttle/pull/125)), a trip
  plan for every event and the when-to-leave page
  ([#126](https://github.com/vchen017/project-yale-shuttle/pull/126),
  [#127](https://github.com/vchen017/project-yale-shuttle/pull/127)), shown as
  a calendar with each trip on a map
  ([#151](https://github.com/vchen017/project-yale-shuttle/pull/151)), where a
  rider can ask the trip agent about a trip
  ([#150](https://github.com/vchen017/project-yale-shuttle/pull/150)).
- **The AI notification flow:** an hourly job that sends the agent's
  notifications for riders' upcoming trips
  ([#142](https://github.com/vchen017/project-yale-shuttle/pull/142)), a setup
  checklist on the notifications page
  ([#145](https://github.com/vchen017/project-yale-shuttle/pull/145)), and an
  evaluation set of 22 cases tested against bad answers and outages
  ([#141](https://github.com/vchen017/project-yale-shuttle/pull/141)).
- **A "Leave by" riders can trust:** the pages and the AI share one
  prediction, the corrected live countdown
  ([#166](https://github.com/vchen017/project-yale-shuttle/pull/166)); "Leave
  by" keeps a few minutes to spare, refitted nightly
  ([#168](https://github.com/vchen017/project-yale-shuttle/pull/168)); the
  accuracy dashboard tracks how often riders make their bus
  ([#167](https://github.com/vchen017/project-yale-shuttle/pull/167)); and the
  when-to-leave page opens with the next trip, live
  ([#169](https://github.com/vchen017/project-yale-shuttle/pull/169)); and the
  board's footnote now says the same in one line
  ([#170](https://github.com/vchen017/project-yale-shuttle/pull/170)). On
  recorded buses, riders who leave when we say now make their bus 92% of the
  time, up from 49%.
- **Also:** help choosing where to get off during a detour
  ([#129](https://github.com/vchen017/project-yale-shuttle/pull/129)), sharing
  a live trip ([#146](https://github.com/vchen017/project-yale-shuttle/pull/146)),
  a trip page that leads with one answer
  ([#132](https://github.com/vchen017/project-yale-shuttle/pull/132),
  [#134](https://github.com/vchen017/project-yale-shuttle/pull/134)), jobs that
  start on time ([#130](https://github.com/vchen017/project-yale-shuttle/pull/130)),
  `technical.md` ([#143](https://github.com/vchen017/project-yale-shuttle/pull/143)),
  and four audit fixes
  ([#135](https://github.com/vchen017/project-yale-shuttle/pull/135) to
  [#138](https://github.com/vchen017/project-yale-shuttle/pull/138)).

## What works on staging and production

As of Nov 25, production serves `688908a`, promoted through the promote
workflow ([run](https://github.com/vchen017/project-yale-shuttle/actions/runs/45653483491)),
and staging tracks `main`, one footnote ahead:
everything from Sprint 2, plus connecting a calendar, the when-to-leave
calendar with each trip's map and the live next trip, asking the trip agent
about a trip, detour badges and shared live trips. The rider grader account
has a demo calendar, so its when-to-leave page always has trips to show.

## Working with our agents

Our detour badges ([#129](https://github.com/vchen017/project-yale-shuttle/pull/129))
passed every mocked test, and our agent's replay of the recorded polls, at
their five-minute pace, found nothing wrong. Replayed at the pace riders
load the page, every two minutes, the routes' usual loops and garage runs
showed 35 false detours in 12 days. A nightly job that learns each route's
usual shapes fixed it. What we changed: `AGENTS.md` now asks for every push
to be checked against real data, at the pace the code really runs
([#140](https://github.com/vchen017/project-yale-shuttle/pull/140),
[#144](https://github.com/vchen017/project-yale-shuttle/pull/144)). The same
habit later showed that riders following our own "Leave by" made their bus
only 49% of the time (#153).

## Backlog changes for Sprint 4

Four stories planned for Sprint 4 shipped in this sprint (#153, #155, #156,
#157), with [#14](https://github.com/vchen017/project-yale-shuttle/issues/14)
folded into #157. Sprint 4 keeps better predictions for buses 30+ minutes
away ([#154](https://github.com/vchen017/project-yale-shuttle/issues/154)),
testing the AI's alerts on real recorded trips
([#158](https://github.com/vchen017/project-yale-shuttle/issues/158)),
getting production ready for the A/B run
([#47](https://github.com/vchen017/project-yale-shuttle/issues/47)), deleting
a rider's calendar data ([#9](https://github.com/vchen017/project-yale-shuttle/issues/9)),
and our report, retrospective and checklist
([#45](https://github.com/vchen017/project-yale-shuttle/issues/45),
[#48](https://github.com/vchen017/project-yale-shuttle/issues/48),
[#51](https://github.com/vchen017/project-yale-shuttle/issues/51)). Making the
long waits better ([#118](https://github.com/vchen017/project-yale-shuttle/issues/118))
closed as measured: nothing beat the shipped predictor. Seven stretch ideas
wait in the backlog ([#159](https://github.com/vchen017/project-yale-shuttle/issues/159)
to [#165](https://github.com/vchen017/project-yale-shuttle/issues/165)).
