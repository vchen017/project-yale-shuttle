"""Import the recorded shuttle history from the recorder's GitHub repo (#22).

Reads the recorder's CSVs straight from GitHub, so nothing lasting lands on
the server's disk. Rerunning adds no duplicates: recorded polls are skipped,
and gaps.csv keeps its polls stored as MISSING.

    python manage.py import_history              # everything the recorder has
    python manage.py import_history --days 1     # the latest recorded day
    python manage.py import_history --since 2026-11-01
    python manage.py import_history --date 2026-11-10

Also walks the recorder's routes.json git history, recording when a route's
stop list changed (#57).
"""

import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from history import importer, sources


class Command(BaseCommand):
    help = (
        "Import the recorder's shuttle history (buses, ETAs, route status, "
        "announcements, and the polls it missed) into the database."
    )

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Import only this UTC date (YYYY-MM-DD).")
        parser.add_argument(
            "--days", type=int, help="Import only the last N UTC dates that have data."
        )
        parser.add_argument(
            "--since", help="Import only UTC dates on or after this date (YYYY-MM-DD)."
        )

    def handle(self, *args, **options):
        wanted = self._dates_to_import(options)

        try:
            gaps = importer.import_gaps(sources.read_gaps())
        except OSError as error:
            raise CommandError(f"Could not reach GitHub: {error}")
        self.stdout.write(
            f"gaps.csv: {gaps.polls_added} missing polls stored, "
            f"{gaps.polls_already_known} already known"
        )

        for date in wanted:
            try:
                day = sources.read_day(date)
            except OSError as error:
                raise CommandError(f"Could not fetch {date} from GitHub: {error}")
            stats = importer.import_day(date, day)
            self.stdout.write(
                f"{date}: {stats.polls_added} polls imported "
                f"({stats.buses} bus positions, {stats.etas} ETAs, "
                f"{stats.route_statuses} route statuses, "
                f"{stats.announcements} announcements); "
                f"{stats.polls_skipped} polls already recorded"
            )

        # The recorder's routes.json history: when routes gain or lose stops
        # (#57), so predictions only trust stops the route still serves.
        try:
            commits = sources.list_route_commits()
            routes_stats = importer.import_route_history(commits, sources.read_routes_json)
        except OSError as error:
            raise CommandError(f"Could not fetch routes.json history from GitHub: {error}")
        self.stdout.write(
            f"routes.json: {routes_stats.commits_seen} new commits walked, "
            f"{routes_stats.versions_added} stop-list versions stored, "
            f"{routes_stats.changes_added} changes recorded"
        )

    def _dates_to_import(self, options):
        """Which UTC dates to fetch. Arguments are parsed and checked before
        anything reaches the network, so bad arguments fail fast."""
        date_arg, days_arg, since_arg = options["date"], options["days"], options["since"]
        given = [arg for arg in (date_arg, days_arg, since_arg) if arg is not None]
        if len(given) > 1:
            raise CommandError("Use only one of --date, --days or --since.")

        try:
            date = dt.date.fromisoformat(date_arg) if date_arg else None
            since = dt.date.fromisoformat(since_arg) if since_arg else None
        except ValueError:
            raise CommandError("Dates look like YYYY-MM-DD (UTC dates).")
        if days_arg is not None and days_arg < 1:
            raise CommandError("--days must be at least 1.")

        try:
            dates = sources.list_dates()
        except OSError as error:
            raise CommandError(f"Could not reach GitHub: {error}")
        if date:
            return [date]
        if days_arg is not None:
            return dates[-days_arg:]
        if since:
            return [day for day in dates if day >= since]
        return dates
