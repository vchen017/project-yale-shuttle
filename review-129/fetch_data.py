"""Build the replay's data folders from a clone of vchen017/downtowner-recorder.

Usage: python fetch_data.py <recorder clone> <out dir>

<out>/data   route_status.csv and buses.csv for each replayed day (Nov 9-20),
             data/routes.json as it stood after each commit that changed it
             (routes_hist/<UTC commit time>.json), and as it is now
             (routes_latest.json)
<out>/extra  buses.csv for Oct 26 - Nov 8: the 14 days record_usual_shapes
             looks back over, for the first replayed days
"""

import datetime as dt
import os
import shutil
import subprocess
import sys

REPLAYED = ("2026-11-09", "2026-11-20")
LOOKBACK = ("2026-10-26", "2026-11-08")


def days(first, last):
    day, last = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    while day <= last:
        yield day.isoformat()
        day += dt.timedelta(days=1)


def main(recorder, out):
    for day in days(*REPLAYED):
        os.makedirs(os.path.join(out, "data", day), exist_ok=True)
        for name in ("route_status.csv", "buses.csv"):
            shutil.copy(os.path.join(recorder, "data", day, name), os.path.join(out, "data", day, name))
    for day in days(*LOOKBACK):
        os.makedirs(os.path.join(out, "extra", day), exist_ok=True)
        shutil.copy(os.path.join(recorder, "data", day, "buses.csv"), os.path.join(out, "extra", day, "buses.csv"))

    history = os.path.join(out, "data", "routes_hist")
    os.makedirs(history, exist_ok=True)
    shutil.copy(os.path.join(recorder, "data", "routes.json"), os.path.join(out, "data", "routes_latest.json"))
    commits = subprocess.run(
        ["git", "-C", recorder, "log", "--format=%H %ct", "--", "data/routes.json"],
        capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    for line in filter(None, commits):
        sha, stamp = line.split()
        when = dt.datetime.fromtimestamp(int(stamp), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        routes = subprocess.run(
            ["git", "-C", recorder, "show", f"{sha}:data/routes.json"], capture_output=True, check=True
        ).stdout
        with open(os.path.join(history, f"{when}.json"), "wb") as file:
            file.write(routes)
    print(f"{len(list(days(*REPLAYED)))} replayed days, {len(list(days(*LOOKBACK)))} lookback days, "
          f"{len(os.listdir(history))} versions of routes.json")


if __name__ == "__main__":
    main(*sys.argv[1:3])
