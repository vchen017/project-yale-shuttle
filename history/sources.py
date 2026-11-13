"""Read the recorder's CSVs straight from GitHub — the only network code in
this app, so tests mock this module and nothing else (#22).

The recorder (vchen017/downtowner-recorder) keeps `data/<UTC date>/<file>.csv`
plus `data/gaps.csv` for the polls it failed to save. Nothing is written to
disk: rows are streamed, parsed and handed to the importer.
"""

import csv
import datetime as dt
import io
import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

BRANCH = "main"
TIMEOUT_SECONDS = 60
USER_AGENT = "yale-shuttle-history-import"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _text_stream(url):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    response = urlopen(request, timeout=TIMEOUT_SECONDS)
    return io.TextIOWrapper(response, encoding="utf-8", newline="")  # newline="" for csv


def _raw_url(path, ref=BRANCH):
    repo = settings.HISTORY_RECORDER_REPO
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"


def list_dates():
    """The UTC dates that have data, in order: the directories under data/."""
    repo = settings.HISTORY_RECORDER_REPO
    url = f"https://api.github.com/repos/{repo}/contents/data?ref={BRANCH}"
    with _text_stream(url) as stream:
        entries = json.load(stream)
    names = sorted(entry["name"] for entry in entries if _DATE_RE.match(entry.get("name", "")))
    return [dt.date.fromisoformat(name) for name in names]


def read_rows(path):
    """Yield each row of a recorder CSV as a dict of strings."""
    try:
        stream = _text_stream(_raw_url(path))
    except HTTPError as error:
        if error.code == 404:
            # A date whose file was never written (early Oct 26, before the
            # other CSVs were added); not an error, just no rows.
            return
        raise
    with stream:
        yield from csv.DictReader(stream)


def _parse_datetime(value):
    return dt.datetime.fromisoformat(value) if value else None


def _parse_int(value):
    return int(value) if value else None


def _parse_float(value):
    return float(value) if value else None


def _parse_bool(value):
    return value == "True"


def read_gaps():
    """The recorder's missing polls: (polled_at, cause, counts) tuples.

    The counts are what the recorder's job log saw at that poll, when the log
    kept anything; the recorder never saved rows for these polls.
    """
    rows = []
    for row in read_rows("data/gaps.csv"):
        rows.append(
            {
                "polled_at": _parse_datetime(row["polled_at_utc"]),
                "cause": row.get("cause", "") or "",
                "active_routes": _parse_int(row.get("active_routes", "")),
                "bus_count": _parse_int(row.get("buses", "")),
                "announcement_count": _parse_int(row.get("announcements", "")),
                "eta_count": _parse_int(row.get("etas", "")),
            }
        )
    return rows


def _bus(row):
    return {
        "polled_at": _parse_datetime(row["polled_at_utc"]),
        "bus_id": _parse_int(row["bus_id"]),
        "bus_name": row["bus_name"],
        "route_id": _parse_int(row["route_id"]),
        "route_name": row["route_name"],
        "lat": _parse_float(row["lat"]),
        "lon": _parse_float(row["lon"]),
        "heading": _parse_int(row["heading"]),
        "last_stop_id": _parse_int(row["last_stop_id"]),
        "last_stop_name": row["last_stop_name"],
        "last_update": _parse_datetime(row["last_update_utc"]),
    }


def _eta(row):
    return {
        "polled_at": _parse_datetime(row["polled_at_utc"]),
        "stop_id": _parse_int(row["stop_id"]),
        "stop_name": row["stop_name"],
        "route_id": _parse_int(row["route_id"]),
        "route_name": row["route_name"],
        "bus_id": _parse_int(row["bus_id"]),
        "bus_name": row["bus_name"],
        "eta_minutes": _parse_int(row["eta_minutes"]),
        "calculation_time": _parse_datetime(row["calculation_time_utc"]),
    }


def _route_status(row):
    return {
        "polled_at": _parse_datetime(row["polled_at_utc"]),
        "route_id": _parse_int(row["route_id"]),
        "route_name": row["route_name"],
        "short_name": row["short_name"],
        "description": row["description"],
        "active": _parse_bool(row["active"]),
        "num_stops": _parse_int(row["num_stops"]),
    }


def _announcement(row):
    return {
        "polled_at": _parse_datetime(row["polled_at_utc"]),
        "announcement_id": _parse_int(row["announcement_id"]),
        "title": row["title"],
        "message": row["message"],
        "button_text": row["button_text"],
        "button_url": row["button_url"],
    }


def read_day(date):
    """Fetch one UTC date's four CSVs: parsed rows, keyed by file."""
    prefix = f"data/{date.isoformat()}/"
    return {
        "buses": [_bus(row) for row in read_rows(prefix + "buses.csv")],
        "etas": [_eta(row) for row in read_rows(prefix + "etas.csv")],
        "route_status": [_route_status(row) for row in read_rows(prefix + "route_status.csv")],
        "announcements": [_announcement(row) for row in read_rows(prefix + "announcements.csv")],
    }


def list_route_commits():
    """Commits touching data/routes.json, oldest first, as (sha, committed_at).

    The recorder commits routes.json whenever the file changes, so every
    commit is a candidate stop-list change; the walk diffs consecutive
    versions to find the real ones (#57).
    """
    repo = settings.HISTORY_RECORDER_REPO
    commits = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{repo}/commits"
            f"?path=data/routes.json&per_page=100&page={page}"
        )
        with _text_stream(url) as stream:
            entries = json.load(stream)
        if not entries:
            break
        commits.extend(
            (entry["sha"], _parse_datetime(entry["commit"]["author"]["date"]))
            for entry in entries
        )
        if len(entries) < 100:
            break
        page += 1
    commits.sort(key=lambda item: (item[1], item[0]))
    return commits


def read_routes_json(sha):
    """The recorder's routes.json at a commit, parsed: one dict per route,
    each with its numeric id and ordered stop ids."""
    with _text_stream(_raw_url("data/routes.json", ref=sha)) as stream:
        return json.load(stream)
