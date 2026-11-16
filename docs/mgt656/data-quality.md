# Our shuttle data: gaps, coverage and a spot-check

Kyle's Sprint 1 feedback (#71) asked where our data has gaps, whether the feed
gives enough usable observations for each route, and how far to trust the
arrivals we infer. This is the answer for Nov 9–15, 2026 (UTC). To rerun it for
any week:

```sh
python manage.py data_quality --days 7 --until 2026-11-15
```

It reads the [recorder](https://github.com/vchen017/downtowner-recorder)'s
CSVs straight from GitHub, so it needs no imported history. The method is in
`history/quality.py`.

## What it tells us

- **The recording has been complete since Nov 6.** The recorder missed 480
  polls in all, nearly all on Oct 27–30, when its runs couldn't save, a problem
  since fixed. The other five were on Nov 4–5.
- **Every running route gives us ETAs for its stops at every poll.** The
  weekday daytime routes cover 30 or more stops each.
- **Bus GPS is mostly fresh, except on Blue West.** There, 21% of the
  positions were more than 3 minutes old when we polled, and 7% more than 10
  minutes old. The evening Orange and the weekend Blue have the same problem,
  less often. Riders should be told when a position is old, which is
  [#58](https://github.com/vchen017/project-yale-shuttle/issues/58).
- **The arrivals we infer agree with the buses' own GPS.** The feed reports
  each bus's last stop. When that changes between two polls, the bus reached
  the new stop in that five-minute window. Our inferred arrival for the same
  bus and stop falls inside that window 71% of the time, and within 2 minutes
  of it 87% of the time. When we're off, we're usually early (998 cases, against
  142 late): the countdown we extrapolate from runs a little ahead of the bus.
- **For 23% of the GPS visits we inferred no arrival within 10 minutes.** We
  haven't broken these down yet. One likely cause is a countdown that never
  dropped to 5 minutes or less at a poll, which leaves nothing to extrapolate
  from.
- **The limit:** both sides of the comparison come from polls five minutes
  apart, so this checks that the two agree, not the exact second a bus arrived.
  Noting a few arrivals in person at a stop would give us true ground truth.

## The numbers

### Gaps in the recording
Polls the recorder missed, from `data/gaps.csv`:
| UTC date | Missed polls | Why |
|---|---|---|
| 2026-10-27 | 136 | run_could_not_save (136) |
| 2026-10-28 | 136 | run_could_not_save (136) |
| 2026-10-29 | 109 | run_could_not_save (95), no_run (14) |
| 2026-10-30 | 94 | run_could_not_save (94) |
| 2026-11-04 | 4 | push_failed (3), poll_skipped (1) |
| 2026-11-05 | 1 | push_failed (1) |

480 missed polls in all.
### What each route gave us, 2026-11-09 to 2026-11-15 (UTC)
| Route | Polls with ETAs | Stops with ETAs | GPS positions | Older than 3 min | Older than 10 min |
|---|---|---|---|---|---|
| Purple - West Campus | 1555 | 11 | 3610 | 0% | 0% |
| Green - West Campus | 1119 | 20 | 2851 | 0% | 0% |
| Pink - VA Hospital / Med School | 785 | 12 | 1550 | 0% | 0% |
| Brown Connector | 760 | 10 | 757 | 0% | 0% |
| Orange - Weekday Daytime | 667 | 33 | 1242 | 0% | 0% |
| Red - Weekday Daytime | 664 | 30 | 1910 | 0% | 0% |
| Blue - Weekday Daytime | 654 | 31 | 1863 | 0% | 0% |
| Gold Route | 583 | 11 | 583 | 0% | 0% |
| Blue - Night | 514 | 20 | 981 | 0% | 0% |
| Blue West | 507 | 11 | 507 | 21% | 7% |
| Orange - Night | 506 | 26 | 917 | 3% | 2% |
| Orange East | 504 | 12 | 504 | 0% | 0% |
| Blue - Weekend Daytime | 273 | 31 | 273 | 6% | 5% |
| Weekend Grocery (to Hamden) | 242 | 7 | 241 | 0% | 0% |

### Spot-check: inferred arrivals against the buses' own GPS
The buses' GPS put a bus at a new stop 11,770 times. For 9,034 of them (77%) the history inferred an arrival for the same bus and stop within 10 minutes:
- inside the GPS window: 6,455 (71%)
- within 2 minutes of it: 7,894 (87%)
- more than 2 minutes early: 998; more than 2 minutes late: 142
- median distance from the window: 0.0 minutes
A few of them:
- Building 800: GPS says the bus got there between Sun Nov 8, 7:00 PM and 7:04 PM; we inferred 7:02 PM.
- 333 Cedar: GPS says the bus got there between Mon Nov 9, 7:05 PM and 7:09 PM; we inferred 7:06 PM.
- Building 900: GPS says the bus got there between Tue Nov 10, 7:55 PM and 8:00 PM; we inferred 7:56 PM.
- Building 600: GPS says the bus got there between Wed Nov 11, 7:00 PM and 7:05 PM; we inferred 7:00 PM.
- Canner / Whitney (N): GPS says the bus got there between Thu Nov 12, 7:00 PM and 7:04 PM; we inferred 7:04 PM.
