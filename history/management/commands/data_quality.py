"""Report on the quality of the recorded shuttle data, as Markdown (#74).

    python manage.py data_quality                     # the last 7 full UTC days
    python manage.py data_quality --days 3 --until 2026-11-15

Reads the recorder's CSVs straight from GitHub, like import_history, so it
needs no imported history. See history/quality.py for the method.
"""

import datetime as dt
from collections import defaultdict
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError

from history import quality, sources

EASTERN = ZoneInfo("America/New_York")


def _eastern(moment):
    return moment.astimezone(EASTERN).strftime("%a %b %-d, %-I:%M %p")


class Command(BaseCommand):
    help = "Report on the recorded data's gaps, GPS staleness and coverage, and spot-check inferred arrivals."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="How many UTC days to check.")
        parser.add_argument("--until", help="The last UTC day to check (YYYY-MM-DD); yesterday by default.")

    def handle(self, *args, days, until, **options):
        try:
            last = dt.date.fromisoformat(until) if until else dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
        except ValueError:
            raise CommandError("--until looks like YYYY-MM-DD.") from None
        dates = [last - dt.timedelta(days=offset) for offset in range(days - 1, -1, -1)]

        polls, stops = defaultdict(set), defaultdict(set)
        bus_rows, offsets, missed, examples, names = [], [], [], [], {}
        for date in dates:
            self.stderr.write(f"Reading {date}…")
            try:
                eta_rows = list(sources.read_rows(f"data/{date}/etas.csv"))
                day_buses = list(sources.read_rows(f"data/{date}/buses.csv"))
            except OSError as error:
                raise CommandError(f"Could not read {date} from GitHub: {error}") from None
            names.update((int(row["stop_id"]), row["stop_name"]) for row in eta_rows)
            quality.add_coverage(eta_rows, polls, stops)
            served = {(int(row["route_id"]), int(row["stop_id"])) for row in eta_rows}
            arrivals = quality.inferred_arrivals(eta_rows)
            del eta_rows
            visits = quality.gps_visits(day_buses)
            day_offsets, day_missed = quality.spot_check(visits, arrivals, served)
            offsets += day_offsets
            missed += day_missed
            examples += self._examples(visits, arrivals, served)
            bus_rows += day_buses

        self._gaps()
        self._coverage(dates, polls, stops, quality.gps_staleness(bus_rows))
        self._spot_check(offsets, missed, examples, names)

    def _write(self, text=""):
        self.stdout.write(text)

    def _gaps(self):
        gaps = quality.gaps_by_day(sources.read_gaps())
        self._write("## Gaps in the recording\n")
        self._write("Polls the recorder missed, from `data/gaps.csv`:\n")
        self._write("| UTC date | Missed polls | Why |")
        self._write("|---|---|---|")
        for day, causes in gaps.items():
            why = ", ".join(f"{cause} ({count})" for cause, count in causes.most_common())
            self._write(f"| {day} | {sum(causes.values())} | {why} |")
        self._write(f"\n{sum(sum(c.values()) for c in gaps.values())} missed polls in all.\n")

    def _coverage(self, dates, polls, stops, staleness):
        self._write(f"## What each route gave us, {dates[0]} to {dates[-1]} (UTC)\n")
        self._write("| Route | Polls with ETAs | Stops with ETAs | GPS positions | Older than 3 min | Older than 10 min |")
        self._write("|---|---|---|---|---|---|")
        for route in sorted(polls, key=lambda name: -len(polls[name])):
            positions, shares = staleness.get(route, (0, {3: 0, 10: 0}))
            self._write(
                f"| {route} | {len(polls[route])} | {len(stops[route])} | {positions} "
                f"| {shares[3]:.0%} | {shares[10]:.0%} |"
            )
        self._write()

    def _examples(self, visits, arrivals, served, per_day=1):
        """A visit or two a day whose inferred arrival is inside its GPS window."""
        found = []
        for route, stop, bus, after, by in visits:
            if (route, stop) not in served:
                continue
            for arrival in arrivals.get((route, stop, bus), []):
                if quality.offset_minutes(arrival, after, by) == 0 and (by - after) >= dt.timedelta(minutes=4):
                    found.append((route, stop, bus, after, by, arrival))
                    break
            if len(found) >= per_day:
                break
        return found

    def _spot_check(self, offsets, missed, examples, names):
        summary = quality.summarize_offsets(offsets, missed)
        self._write("## Spot-check: inferred arrivals against the buses' own GPS\n")
        if not summary["visits"]:
            self._write("No GPS visits to compare.")
            return
        visits, inferred = summary["visits"], summary["inferred"]
        self._write(
            f"The buses' GPS put a bus at a new stop {visits:,} times. For {inferred:,} of them "
            f"({inferred / visits:.0%}) the history inferred an arrival for the same bus and stop "
            f"within {int(quality.SLACK.total_seconds() // 60)} minutes:\n"
        )
        self._write(f"- inside the GPS window: {summary['inside']:,} ({summary['inside'] / inferred:.0%})")
        self._write(f"- within 2 minutes of it: {summary['within_2']:,} ({summary['within_2'] / inferred:.0%})")
        self._write(f"- more than 2 minutes early: {summary['early']:,}; more than 2 minutes late: {summary['late']:,}")
        self._write(f"- median distance from the window: {summary['median_abs']:.1f} minutes\n")
        self._write("A few of them:\n")
        for route, stop, bus, after, by, arrival in examples[:5]:
            self._write(
                f"- {names.get(stop, stop)}: GPS says the bus got there between {_eastern(after)} "
                f"and {by.astimezone(EASTERN):%-I:%M %p}; we inferred {arrival.astimezone(EASTERN):%-I:%M %p}."
            )
