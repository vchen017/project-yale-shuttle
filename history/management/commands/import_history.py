"""Import the recorded shuttle history from the recorder's GitHub repo (#22).

Reads the recorder's CSVs straight from GitHub, so nothing lasting lands on
the server's disk. Rerunning adds no duplicates: recorded polls are skipped,
and gaps.csv keeps its polls stored as MISSING.

    python manage.py import_history              # everything the recorder has
    python manage.py import_history --days 1     # the latest recorded day
    python manage.py import_history --since 2026-11-01
    python manage.py import_history --date 2026-11-10
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

        gaps = importer.import_gaps(sources.read_gaps())
        self.stdout.write(
            f"gaps.csv: {gaps.polls_added} missing polls stored, "
            f"{gaps.polls_already_known} already known"
        )

        for date in wanted:
            day = sources.read_day(date)
            stats = importer.import_day(date, day)
            self.stdout.write(
                f"{date}: {stats.polls_added} polls imported "
                f"({stats.buses} bus positions, {stats.etas} ETAs, "
                f"{stats.route_statuses} route statuses, "
                f"{stats.announcements} announcements); "
                f"{stats.polls_skipped} polls already recorded"
            )

    def _dates_to_import(self, options):
        date_arg, days_arg, since_arg = options["date"], options["days"], options["since"]
        given = [arg for arg in (date_arg, days_arg, since_arg) if arg is not None]
        if len(given) > 1:
            raise CommandError("Use only one of --date, --days or --since.")

        try:
            if date_arg:
                return [dt.date.fromisoformat(date_arg)]
            dates = sources.list_dates()
            if days_arg is not None:
                if days_arg < 1:
                    raise CommandError("--days must be at least 1.")
                return dates[-days_arg:]
            if since_arg:
                since = dt.date.fromisoformat(since_arg)
                return [date for date in dates if date >= since]
            return dates
        except ValueError:
            raise CommandError("Dates look like YYYY-MM-DD (UTC dates).")
        except OSError as error:
            raise CommandError(f"Could not reach GitHub: {error}")
