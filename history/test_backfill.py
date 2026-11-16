"""The history import's --backfill, and the daily job that uses it: filling in
the days the predictions learn from when a database doesn't have them yet.
GitHub is mocked, and so is the import of each day: these tests are about which
days get imported. In its own file, so it doesn't collide with other pull
requests that add to history/tests.py."""

import datetime as dt
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from history import jobs
from history.models import Poll

COMMAND = "history.management.commands.import_history"


def day(days_ago):
    return timezone.now().date() - dt.timedelta(days=days_ago)


def noon(date):
    return dt.datetime.combine(date, dt.time(12), tzinfo=dt.UTC)


class BackfillTests(TestCase):
    def run_import(self, recorded_days, *args):
        """The dates the command imports, in order, and what it printed."""
        stats = SimpleNamespace(polls_added=0, buses=0, etas=0, route_statuses=0, announcements=0, polls_skipped=0)
        out = StringIO()
        with (
            mock.patch(f"{COMMAND}.sources.list_dates", return_value=sorted(recorded_days)),
            mock.patch(f"{COMMAND}.sources.read_gaps", return_value=[]),
            mock.patch(f"{COMMAND}.sources.read_day", return_value={}),
            mock.patch(f"{COMMAND}.sources.list_route_commits", return_value=[]),
            mock.patch(f"{COMMAND}.importer.import_day", return_value=stats) as import_day,
        ):
            call_command("import_history", *args, stdout=out)
        return [call.args[0] for call in import_day.call_args_list], out.getvalue()

    def test_fills_in_older_days_with_no_recorded_polls_newest_first(self):
        Poll.objects.create(polled_at=noon(day(5)))  # already loaded
        imported, printed = self.run_import([day(n) for n in range(10)], "--days", "2", "--backfill", "3")
        self.assertEqual(imported, [day(1), day(0), day(2), day(3), day(4)])
        self.assertIn("backfill: 3 earlier days imported, 4 still to import", printed)

    def test_a_day_with_only_missing_polls_still_needs_its_recorded_ones(self):
        # gaps.csv stores MISSING polls for days whose recorded rows never came in.
        Poll.objects.create(polled_at=noon(day(3)), status=Poll.Status.MISSING, cause="no_run")
        imported, _ = self.run_import([day(n) for n in range(5)], "--days", "1", "--backfill", "5")
        self.assertIn(day(3), imported)

    def test_days_older_than_the_predictions_window_are_left_alone(self):
        imported, printed = self.run_import([day(40), day(29), day(28), day(0)], "--days", "1", "--backfill", "5")
        self.assertEqual(imported, [day(0), day(28)])
        self.assertIn("backfill: 1 earlier day imported, 0 still to import", printed)

    def test_without_backfill_nothing_more_is_imported(self):
        imported, printed = self.run_import([day(n) for n in range(5)], "--days", "1")
        self.assertEqual(imported, [day(0)])
        self.assertNotIn("backfill", printed)

    def test_backfill_must_be_at_least_one(self):
        with self.assertRaisesMessage(CommandError, "--backfill must be at least 1."):
            call_command("import_history", "--backfill", "0", stdout=StringIO())


class DailyJobTests(TestCase):
    def test_the_daily_job_loads_three_days_and_fills_in_up_to_five_more(self):
        with mock.patch("history.jobs.call_command") as command:
            jobs.import_history()
        command.assert_called_once_with("import_history", days=3, backfill=5, stdout=mock.ANY)
