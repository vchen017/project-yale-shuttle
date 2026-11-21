# Replaying #129's detour detection over the recorded polls

These are the scripts behind the review of #129 at `e5405cd`, so anyone can rerun its numbers. This branch holds only these files and isn't meant to merge.

Everything runs on a throwaway local SQLite database. `boot.py` blocks every outbound connection, so nothing reaches the feed, Neon or GitHub. You need Python 3.12 with the repo's requirements.

## 1. Get the data

You need a clone of [downtowner-recorder](https://github.com/vchen017/downtowner-recorder):

```sh
python fetch_data.py ~/downtowner-recorder ~/replay129
```

This copies three things:
- `route_status.csv` and `buses.csv` for Nov 9–20, into `~/replay129/data`;
- `buses.csv` for Oct 26–Nov 8, into `~/replay129/extra`. These are the 14 days `record_usual_shapes` looks back over;
- `data/routes.json` as it stood after each commit, so each moment uses the paths of its own day.

## 2. Replay

Check out the PR first, for example `git fetch origin pull/129/head && git worktree add ~/pr129 e5405cd`. Then:

```sh
python replay_new.py ~/pr129 ~/replay129/data ~/replay129/extra c2.json --cadence 2 --positions hold --usual leakfree
python classify.py ~/pr129 ~/replay129/data c2.json
```

How the replay runs:
- **Page loads:** one every 2 minutes. Each calls the real `detoured_routes()` against the real `Sample` table.
- **Bus positions:** each load holds the last 5-minute poll's positions.
- **Usual shapes:** `record_usual_shapes` runs at 9:41 UTC each day, as `jobs.yml` schedules it. It reads only what the 9:17 import had loaded by then, so no day sees its own evening.

Result: 35 flags, listed in `flags-nov9-20.txt`:
- 12 buses heading to the garage;
- 20 usual evening loops;
- 3 one-offs, including Green's detour on Nov 12.

Other options:
- `--cadence 5` (a load at each poll): 33 flags.
- `--usual whole` (one usual-shapes run over the whole window): 33 flags.
- `--usual none` (nothing suppressed): 45 flags.
- With `REPLAY_STRING_KEYS=1` set, samples come back with string keys, as before the PR's JSON fix: 0 flags.

## 3. The tests that fail from Nov 25

```sh
python clock_test.py ~/pr129 2026-11-25T02:00:00+00:00 detours.tests.UsualShapeJobTests
```

`clock_test.py` runs the tests with `timezone.now()` set to that moment. At `2026-11-24T23:00:00+00:00` the same two tests pass.

## 4. One bus

```sh
python trace_bus_db.py ~/pr129 ~/replay129/data 69307 2026-11-18T23:30:00+00:00 2026-11-19T00:30:00+00:00
```

This traces Pink's bus on Nov 18. It was on its path at 6:40 PM, then off for the next 7 polls, up to 5 km away, with the route still active. It left the feed at 7:20 PM.
