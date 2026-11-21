"""For each flag episode riders would see in a replay_new.py run, what the
culprit bus actually did, from the recorded 5-minute polls: where it was
while flagged, how far off its route's path, and how the off-run ended —
back on the path (an excursion: a loop or a detour), or out of service (the
bus left the feed / its route went inactive: the run to the garage).
Recurrence counts the distinct days the same route had a raw (unsuppressed)
flag within 600 m of the same spot.

Usage: python classify.py <repo dir> <data dir> <replay out.json> [--raw]
"""

import bisect
import csv
import datetime as dt
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boot import boot  # noqa: E402

repo, data, out = sys.argv[1:4]
use_raw = "--raw" in sys.argv
boot(repo, out + ".classify.sqlite3")
from detours import detect  # noqa: E402

ET = detect.ET
res = json.load(open(out))
hist_dir = os.path.join(data, "routes_hist")
hist_times = sorted(dt.datetime.fromisoformat(n[:-5].replace("Z", "+00:00")) for n in os.listdir(hist_dir))
hist_cache = {}


def paths_at(t):
    key = hist_times[max(0, bisect.bisect_right(hist_times, t) - 1)]
    if key not in hist_cache:
        routes = json.load(open(os.path.join(hist_dir, key.strftime("%Y-%m-%dT%H:%M:%SZ") + ".json")))
        hist_cache[key] = {r["id"]: r.get("path") or [] for r in routes}
    return hist_cache[key]


active = defaultdict(set)
seen = defaultdict(dict)  # bus -> {t: (route, lat, lon, age, last_stop)}
poll_times = set()
for day in sorted(d for d in os.listdir(data) if d.startswith("2026-")):
    for row in csv.DictReader(open(os.path.join(data, day, "route_status.csv"))):
        t = dt.datetime.fromisoformat(row["polled_at_utc"])
        poll_times.add(t)
        if row["active"] == "True":
            active[t].add(int(row["route_id"]))
    for row in csv.DictReader(open(os.path.join(data, day, "buses.csv"))):
        t = dt.datetime.fromisoformat(row["polled_at_utc"])
        age = (t - dt.datetime.fromisoformat(row["last_update_utc"])).total_seconds()
        seen[int(row["bus_id"])][t] = (int(row["route_id"]), float(row["lat"]), float(row["lon"]), age, row["last_stop_name"])
poll_times = sorted(poll_times)


def off_m(route, lat, lon, t):
    return detect.meters_off_path((lat, lon), paths_at(t).get(route, []))


def dist(a, b):
    x = (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0]))
    y = (a[0] - b[0]) * 111320
    return math.hypot(x, y)


def describe(route_id, bus, first, last):
    track = seen.get(bus, {})
    i0 = bisect.bisect_left(poll_times, first - dt.timedelta(minutes=30))
    i1 = bisect.bisect_right(poll_times, last + dt.timedelta(minutes=90))
    window = poll_times[i0:i1]
    offs = []
    for t in window:
        o = track.get(t)
        if o and first - dt.timedelta(minutes=15) <= t <= last and o[0] == route_id:
            m = off_m(route_id, o[1], o[2], t)
            if m > detect.OFF_PATH_METERS:
                offs.append((t, m, o[1], o[2]))
    ending = "?"
    for t in window:
        if t <= last:
            continue
        o = track.get(t)
        if route_id not in active[t]:
            ending = f"route inactive from {t.astimezone(ET):%I:%M %p}"
            break
        if o is None:
            ending = f"bus gone from feed {t.astimezone(ET):%I:%M %p}"
            break
        if o[0] != route_id:
            ending = f"bus moved to route {o[0]} {t.astimezone(ET):%I:%M %p}"
            break
        if off_m(route_id, o[1], o[2], t) <= detect.OFF_PATH_METERS:
            ending = f"back on path {t.astimezone(ET):%I:%M %p}"
            break
    if offs:
        c = (sum(x[2] for x in offs) / len(offs), sum(x[3] for x in offs) / len(offs))
        mx = max(x[1] for x in offs)
    else:
        c, mx = (None, None), 0
    stops = []
    for t in window:
        o = track.get(t)
        if o and first - dt.timedelta(minutes=10) <= t <= last + dt.timedelta(minutes=10) and o[4] and o[4] not in stops:
            stops.append(o[4])
    return c, mx, ending, stops


# every raw episode's spot, for recurrence
records = res["records"]
by_route = defaultdict(list)
for r in records:
    by_route[r["route_id"]].append(r)
raw_spots = []  # (route, date ET, centroid)
gap = dt.timedelta(minutes=res["args"]["cadence"], seconds=61)
for rid, rs in by_route.items():
    rs.sort(key=lambda r: r["t"])
    cur = None
    for r in rs:
        t = dt.datetime.fromisoformat(r["t"])
        if cur and t - cur[1] <= gap:
            cur[1] = t
            cur[2] |= set(r["buses"])
        else:
            cur = [t, t, set(r["buses"])]
            raw_spots.append((rid, cur))
spots = []
for rid, (f, l, buses) in raw_spots:
    for b in buses:
        c, _, _, _ = describe(rid, b, f, l)
        if c[0] is not None:
            spots.append((rid, f.astimezone(ET).date(), c))

eps = res["shown_episodes"]
if use_raw:
    eps = [{"route_id": rid, "route_name": "", "first": f.isoformat(), "last": l.isoformat(), "n": 0, "buses": sorted(b)}
           for rid, (f, l, b) in sorted(raw_spots, key=lambda x: x[1][0])]
utc_days = None
for a in sys.argv:
    if a.startswith("--utc-days="):
        lo, hi = a.split("=", 1)[1].split(",")
        utc_days = (dt.date.fromisoformat(lo), dt.date.fromisoformat(hi))
if utc_days:
    eps = [e for e in eps if utc_days[0] <= dt.datetime.fromisoformat(e["first"]).date() <= utc_days[1]]
print(f"{len(eps)} {'raw' if use_raw else 'shown'} episodes" + (f" (UTC days {utc_days[0]}..{utc_days[1]})" if utc_days else ""))
counts = defaultdict(int)
minutes = defaultdict(float)
cad = res["args"]["cadence"]
for e in eps:
    first, last = dt.datetime.fromisoformat(e["first"]), dt.datetime.fromisoformat(e["last"])
    cats = []
    lines = []
    for b in e["buses"]:
        c, mx, ending, stops = describe(e["route_id"], b, first, last)
        days = {d for rid, d, cc in spots if rid == e["route_id"] and c[0] is not None and dist(c, cc) < 600}
        if ending.startswith(("route inactive", "bus gone", "bus moved")):
            cat = "garage run (left service)"
        elif ending.startswith("back on path") and len(days) >= 3:
            cat = "usual loop (same spot 3+ days)"
        elif ending.startswith("back on path"):
            cat = "one-off, back on path"
        else:
            cat = "unclear"
        cats.append(cat)
        lines.append(
            f"   bus {b}: max {mx:>4.0f} m at ({c[0] or 0:.4f},{c[1] or 0:.4f}); {ending}; same spot on {len(days)} day(s); "
            f"stops {stops[:3]} -> {cat}"
        )
    cat = sorted(cats)[0] if cats else "unclear"
    counts[cat] += 1
    minutes[cat] += (last - first).total_seconds() / 60 + cad
    print(f"{first.astimezone(ET):%a %b %d %I:%M %p}-{last.astimezone(ET):%I:%M %p} route {e['route_id']:>2} {e.get('route_name','')[:22]}")
    for line in lines:
        print(line)
print("by category (episodes, rider-visible minutes):")
for cat in sorted(counts):
    print(f"  {cat:<34} {counts[cat]:>3}  ~{minutes[cat]:>5.0f} min")
