"""Replay the recorded Downtowner polls through PR #129 at e5405cd, the way
production would run it:

- every page load calls the real detours.detect.detoured_routes(now=t), which
  writes and reads the real detours.Sample table (local SQLite here);
- the usual-shapes table (detours.UsualOffPath) is filled by the real nightly
  job, detours.jobs.record_usual_shapes, reading the real history.BusPosition
  table that the import fills.

Usage:
  python replay_new.py <repo> <data dir> <extra history dir> <out.json>
      [--cadence 5|2] [--positions hold|interp] [--usual none|leakfree|whole]
      [--usual-cache file.json] [--paths hist|latest] [--start ISO] [--end ISO]

--cadence 5      a page load at every recorded poll, at the poll's own time
--cadence 2      a page load every 2 minutes (production with steady traffic);
                 positions between polls come from --positions:
                   hold    the last poll's positions, fixes aged from the poll
                   interp  straight-line between the two polls around the load
                           (same bus, same route in both; else held)
--usual leakfree the job runs at 9:41 UTC each day, as jobs.yml schedules it,
                 reading only history the 9:17 UTC import had brought in by
                 then (yesterday in full, today so far): no day sees its own
                 future. The extra history dir (Oct 26 - Nov 8 buses.csv from
                 the recorder) gives the first days their 14-day lookback.
--usual whole    the job runs once, at the end of the data, over everything
                 (what the PR's own check did): the replayed days' own
                 evenings decide their usual hours (leakage)
--usual none     the table stays empty (nothing suppressed)
--paths hist     each moment's routes answer is the recorder's routes.json as
                 it stood then; `latest` uses today's (the "live feed paths")
The routes' `active` flags always come from the poll's route_status.csv.
"""

import argparse
import bisect
import csv
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boot import boot  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("repo")
ap.add_argument("data")
ap.add_argument("extra")
ap.add_argument("out")
ap.add_argument("--cadence", type=int, default=5, choices=[2, 5])
ap.add_argument("--positions", default="hold", choices=["hold", "interp"])
ap.add_argument("--usual", default="leakfree", choices=["none", "leakfree", "whole"])
ap.add_argument("--usual-cache", default="")
ap.add_argument("--paths", default="hist", choices=["hist", "latest"])
ap.add_argument("--active", default="poll", choices=["poll", "latest"])
ap.add_argument("--start", default="")
ap.add_argument("--end", default="")
args = ap.parse_args()

boot(args.repo, args.out + ".sqlite3")

from django.core.cache import cache  # noqa: E402
from django.utils import timezone as djtz  # noqa: E402

from detours import detect, jobs  # noqa: E402
from detours.models import Sample, UsualOffPath  # noqa: E402
from history.models import BusPosition, Poll  # noqa: E402
from transit import downtowner  # noqa: E402

UTC = dt.timezone.utc
ET = detect.ET

if os.environ.get("REPLAY_STRING_KEYS"):
    # What a replay sees if samples come back from the JSON column without
    # the int normalization (the bug the PR text says replaying caught).
    _real_samples_at = detect._samples_at

    def _samples_at_string_keys(now_dt):
        return [
            {"at": s["at"], "routes": {str(r): {str(b): o for b, o in bs.items()} for r, bs in s["routes"].items()}}
            for s in _real_samples_at(now_dt)
        ]

    detect._samples_at = _samples_at_string_keys


def parse_t(s):
    return dt.datetime.fromisoformat(s)


# --- routes answers ----------------------------------------------------------
hist_dir = os.path.join(args.data, "routes_hist")
hist_times = sorted(parse_t(n[:-5].replace("Z", "+00:00")) for n in os.listdir(hist_dir))
hist_cache = {}
latest_routes = json.load(open(os.path.join(args.data, "routes_latest.json")))


def routes_at(t):
    if args.paths == "latest":
        return latest_routes
    key = hist_times[max(0, bisect.bisect_right(hist_times, t) - 1)]
    if key not in hist_cache:
        name = key.strftime("%Y-%m-%dT%H:%M:%SZ") + ".json"
        hist_cache[key] = json.load(open(os.path.join(hist_dir, name)))
    return hist_cache[key]


# --- recorded polls (the replayed days) ---------------------------------------
days = sorted(d for d in os.listdir(args.data) if d.startswith("2026-"))
polls = {}
for day in days:
    for row in csv.DictReader(open(os.path.join(args.data, day, "route_status.csv"))):
        t = parse_t(row["polled_at_utc"])
        polls.setdefault(t, {"buses": [], "active": set()})
        if row["active"] == "True":
            polls[t]["active"].add(int(row["route_id"]))
    for row in csv.DictReader(open(os.path.join(args.data, day, "buses.csv"))):
        t = parse_t(row["polled_at_utc"])
        polls.setdefault(t, {"buses": [], "active": set()})
        polls[t]["buses"].append(
            {
                "id": int(row["bus_id"]),
                "name": row["bus_name"],
                "route": int(row["route_id"]),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "lastUpdate": parse_t(row["last_update_utc"]).timestamp(),
            }
        )
poll_times = sorted(polls)

# --- history rows for the job (what history.BusPosition holds) -----------------
hist_rows = []
for base in (args.extra, args.data):
    for day in sorted(d for d in os.listdir(base) if d.startswith("2026-")):
        path = os.path.join(base, day, "buses.csv")
        if os.path.exists(path):
            for row in csv.DictReader(open(path)):
                hist_rows.append((parse_t(row["polled_at_utc"]), row))
hist_rows.sort(key=lambda r: r[0])
hist_i = 0


def insert_history_until(cutoff):
    """Import every recorded bus row polled before `cutoff`, as
    history.importer would (one Poll per polled_at, one BusPosition per row)."""
    global hist_i
    batch = []
    while hist_i < len(hist_rows) and hist_rows[hist_i][0] < cutoff:
        batch.append(hist_rows[hist_i])
        hist_i += 1
    if not batch:
        return 0
    stamps = sorted({t for t, _ in batch})
    Poll.objects.bulk_create([Poll(polled_at=t) for t in stamps], ignore_conflicts=True)
    ids = dict(Poll.objects.filter(polled_at__in=stamps).values_list("polled_at", "id"))
    BusPosition.objects.bulk_create(
        [
            BusPosition(
                poll_id=ids[t],
                polled_at=t,
                bus_id=int(r["bus_id"]),
                bus_name=r["bus_name"],
                route_id=int(r["route_id"]),
                route_name=r["route_name"],
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                heading=int(r["heading"]) if r["heading"] else None,
                last_stop_id=int(r["last_stop_id"]) if r["last_stop_id"] else None,
                last_stop_name=r["last_stop_name"],
                last_update=parse_t(r["last_update_utc"]) if r["last_update_utc"] else None,
            )
            for t, r in batch
        ],
        batch_size=2000,
    )
    return len(batch)


# --- the fake feed -------------------------------------------------------------
state = {"routes_t": None, "active": set(), "buses": []}


def fake_routes():
    answer = []
    for route in routes_at(state["routes_t"]):
        route = dict(route)
        route["active"] = route["id"] in state["active"]
        answer.append(route)
    return answer


def fake_buses():
    return [dict(b) for b in state["buses"]]


downtowner.routes = fake_routes
downtowner.buses = fake_buses


def buses_at(t):
    i = bisect.bisect_right(poll_times, t) - 1
    p = poll_times[i]
    shift = (t - p).total_seconds()
    held = [dict(b, lastUpdate=b["lastUpdate"] + shift) for b in polls[p]["buses"]]
    if args.positions == "hold" or shift == 0 or i + 1 >= len(poll_times):
        return p, held
    q = poll_times[i + 1]
    span = (q - p).total_seconds()
    if span > 360:
        return p, held
    nxt = {b["id"]: b for b in polls[q]["buses"]}
    frac = shift / span
    out = []
    for b in held:
        n = nxt.get(b["id"])
        if n is not None and n["route"] == b["route"]:
            b = dict(b, lat=b["lat"] + frac * (n["lat"] - b["lat"]), lon=b["lon"] + frac * (n["lon"] - b["lon"]))
        out.append(b)
    return p, out


# --- the nightly job -------------------------------------------------------------
job_cache = {}
if args.usual_cache and os.path.exists(args.usual_cache):
    job_cache = json.load(open(args.usual_cache))
job_log = {}


def run_job(J, import_cutoff):
    key = f"{J.isoformat()}|{import_cutoff.isoformat()}|{args.paths}"
    if key in job_cache:
        table, summary = job_cache[key]
        UsualOffPath.objects.all().delete()
        for rid, hours in table.items():
            UsualOffPath.objects.create(route_id=int(rid), hours=hours, window_days=jobs.WINDOW_DAYS, updated_at=J)
    else:
        insert_history_until(import_cutoff)
        saved = dict(state)
        state.update(routes_t=J, active=set(), buses=[])
        with mock.patch.object(djtz, "now", return_value=J):
            summary = jobs.record_usual_shapes()  # the real job, on the real tables
        state.update(saved)
        table = {str(r.route_id): list(r.hours) for r in UsualOffPath.objects.all()}
        job_cache[key] = (table, summary)
        if args.usual_cache:
            json.dump(job_cache, open(args.usual_cache, "w"), indent=1)
    job_log[J.isoformat()] = table
    return table


start = parse_t(args.start) if args.start else poll_times[0]
end = parse_t(args.end) if args.end else poll_times[-1]
first = max(start, poll_times[0])

jobs_due = []
if args.usual == "leakfree":
    d = (first - dt.timedelta(days=1)).date()
    while True:
        J = dt.datetime(d.year, d.month, d.day, 9, 41, tzinfo=UTC)
        if J > end:
            break
        jobs_due.append((J, J - dt.timedelta(minutes=24)))  # the 9:17 import's cutoff
        d += dt.timedelta(days=1)
elif args.usual == "whole":
    J = poll_times[-1] + dt.timedelta(minutes=5)
    run_job(J, J + dt.timedelta(days=1))

# --- page loads ------------------------------------------------------------------
if args.cadence == 5:
    loads = [p for p in poll_times if first <= p <= end]
else:
    t = first.replace(second=0, microsecond=0)
    loads = []
    while t <= end:
        loads.append(t)
        t += dt.timedelta(minutes=2)

records = []
mismatch = 0
for t in loads:
    while jobs_due and jobs_due[0][0] <= t:
        J, cutoff = jobs_due.pop(0)
        run_job(J, cutoff)
    p, buses = buses_at(t)
    act = polls[p]["active"] if args.active == "poll" else {r["id"] for r in latest_routes if r.get("active")}
    state.update(routes_t=t, active=act, buses=buses)
    cache.delete(detect._CACHE_KEY)  # loads are >= 2 minutes apart: the 60 s cache has expired
    shown = detect.detoured_routes(now=t)
    shown_ids = {f["route_id"] for f in shown}
    samples = detect._samples_at(t)
    active = {r["id"]: r for r in fake_routes() if r.get("active")}
    raw = detect.flags_from_samples(samples, active, t, None)
    if not shown_ids <= {f["route_id"] for f in raw}:
        mismatch += 1
    now_ts = t.timestamp()
    hist = [s for s in samples if now_ts - s["at"] <= detect.SAMPLES_HORIZON.total_seconds()]
    recent = [s for s in hist if now_ts - s["at"] <= detect.SAMPLE_SPAN.total_seconds()]
    for f in raw:
        rid = f["route_id"]
        streaks = detect._bus_streaks(hist, rid)
        culprits = [
            bid
            for bid, s in streaks.items()
            if s["saw_on_path"]
            and s["off_run"] >= detect.CONSECUTIVE_POLLS
            and recent[-1]["at"] - s["off_since"] >= detect.MIN_SPAN.total_seconds()
        ]
        records.append(
            {
                "t": t.isoformat(),
                "route_id": rid,
                "route_name": f["route_name"],
                "shown": rid in shown_ids,
                "buses": culprits,
                "off_since": {str(b): streaks[b]["off_since"] for b in culprits},
                "hour_et": t.astimezone(ET).hour,
            }
        )

# --- episodes --------------------------------------------------------------------
gap = dt.timedelta(minutes=args.cadence, seconds=61)


def episodes(recs):
    by_route = defaultdict(list)
    for r in recs:
        by_route[(r["route_id"], r["route_name"])].append(r)
    eps = []
    for (rid, name), rs in by_route.items():
        rs.sort(key=lambda r: r["t"])
        cur = None
        for r in rs:
            t = parse_t(r["t"])
            if cur and t - cur["last"] <= gap:
                cur["last"] = t
                cur["n"] += 1
                cur["buses"] |= set(r["buses"])
            else:
                cur = {"route_id": rid, "route_name": name, "first": t, "last": t, "n": 1, "buses": set(r["buses"])}
                eps.append(cur)
    eps.sort(key=lambda e: e["first"])
    return eps


shown_eps = episodes([r for r in records if r["shown"]])
supp_eps = episodes([r for r in records if not r["shown"]])
raw_eps = episodes(records)

json.dump(
    {
        "args": vars(args),
        "loads": len(loads),
        "records": records,
        "jobs": job_log,
        "shown_episodes": [
            dict(e, first=e["first"].isoformat(), last=e["last"].isoformat(), buses=sorted(e["buses"]))
            for e in shown_eps
        ],
    },
    open(args.out, "w"),
    indent=1,
)

print(
    f"cadence={args.cadence} positions={args.positions} usual={args.usual} paths={args.paths} "
    f"loads={len(loads)} {loads[0].astimezone(ET):%b %d %H:%M}..{loads[-1].astimezone(ET):%b %d %H:%M} ET"
)
print(f"raw (unsuppressed) flagged loads={len(records)} in {len(raw_eps)} episodes; "
      f"shown flagged loads={sum(r['shown'] for r in records)} in {len(shown_eps)} episodes; "
      f"suppressed episodes={len(supp_eps)}; consistency mismatches={mismatch}")
if args.usual != "none":
    print("usual hours (route: Eastern hours) per job run:")
    for J, table in job_log.items():
        print(f"  {J[:16]}  " + ", ".join(f"{rid}:{h}" for rid, h in sorted(table.items(), key=lambda kv: int(kv[0]))))
print("shown episodes:")
for e in shown_eps:
    mins = (e["last"] - e["first"]).total_seconds() / 60 + args.cadence
    print(
        f"  {e['first'].astimezone(ET):%a %b %d %I:%M %p}-{e['last'].astimezone(ET):%I:%M %p} ET "
        f"route {e['route_id']:>2} {e['route_name'][:26]:<26} ~{mins:>3.0f} min  loads {e['n']:>3}  buses {sorted(e['buses'])}"
    )
