"""Print one bus's recorded trajectory around a time, with its distance from
its route's official path (PR #129's meters_off_path) and whether the route
was active.

Usage: python trace_bus.py <repo dir> <data dir> <bus id> <UTC start ISO> <UTC end ISO>
"""
import bisect, csv, datetime as dt, json, os, sys
repo, data, bus, start, end = sys.argv[1:6]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boot import boot
boot(repo, os.path.abspath("trace.sqlite3"))  # a throwaway database in the current folder
from detours import detect
ET = dt.timezone(dt.timedelta(hours=-5))
start = dt.datetime.fromisoformat(start); end = dt.datetime.fromisoformat(end)
hist_dir = os.path.join(data, "routes_hist")
hist = sorted(dt.datetime.fromisoformat(n[:-5].replace("Z", "+00:00")) for n in os.listdir(hist_dir))
def routes_at(t):
    k = hist[max(0, bisect.bisect_right(hist, t) - 1)]
    return {r["id"]: r for r in json.load(open(os.path.join(hist_dir, k.strftime("%Y-%m-%dT%H:%M:%SZ") + ".json")))}
days = sorted({(start + dt.timedelta(hours=h)).strftime("%Y-%m-%d") for h in range(0, int((end - start).total_seconds() // 3600) + 2)})
active = {}
rows = []
for day in days:
    p = os.path.join(data, day)
    if not os.path.isdir(p): continue
    for r in csv.DictReader(open(os.path.join(p, "route_status.csv"))):
        active[(r["polled_at_utc"], r["route_id"])] = r["active"]
    for r in csv.DictReader(open(os.path.join(p, "buses.csv"))):
        t = dt.datetime.fromisoformat(r["polled_at_utc"])
        if r["bus_id"] == bus and start <= t <= end:
            rows.append((t, r))
polls = sorted({dt.datetime.fromisoformat(k[0]) for k in active if start <= dt.datetime.fromisoformat(k[0]) <= end})
seen = {t: r for t, r in rows}
for t in polls:
    r = seen.get(t)
    if r is None:
        print(f"{t.astimezone(ET):%a %I:%M %p}  (bus not in feed)")
        continue
    route = routes_at(t).get(int(r["route_id"]))
    m = detect.meters_off_path((float(r["lat"]), float(r["lon"])), route.get("path") or [])
    age = (t - dt.datetime.fromisoformat(r["last_update_utc"])).total_seconds()
    print(f"{t.astimezone(ET):%a %I:%M %p}  route {r['route_id']:>2} {r['route_name'][:22]:<22} active={active.get((r['polled_at_utc'], r['route_id']))!s:<5} "
          f"({float(r['lat']):.5f},{float(r['lon']):.5f}) off={m:>6.0f} m {'OFF' if m > detect.OFF_PATH_METERS else '   '} fix age {age:>5.0f}s last stop {r['last_stop_name'][:28]}")
