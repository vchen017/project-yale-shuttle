"""The saved accuracy scores (#17): what the score_accuracy command writes
and the dashboard reads. The page never computes a score — scoring a week of
real history takes minutes, past the server's request limit — so these tests
cover the round trip, the command's windows, and the nightly job. Nothing
reaches the network."""

import datetime as dt
import io
import json
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from history import accuracy, jobs, snapshots
from history.management.commands.score_accuracy import windows_for
from history.models import AccuracySnapshot
from history.test_accuracy import DAYS, START, seed_history
from jobs.registry import all_jobs


class SnapshotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_history()
        cls.result = accuracy.accuracy(
            accuracy.eastern_midnight(START), accuracy.eastern_midnight(DAYS[-1] + dt.timedelta(days=1))
        )

    def test_the_payload_round_trips_as_json(self):
        restored = accuracy.from_storage(json.loads(json.dumps(accuracy.to_storage(self.result))))

        self.assertEqual(restored, self.result)
        # What the template relies on survives: real dates, and the stop
        # pairs as tuples again.
        self.assertIsInstance(restored["since"], dt.date)
        self.assertTrue(all(isinstance(day, dt.date) for day in restored["scored_days"]))
        self.assertTrue(all(isinstance(row["key"], tuple) for row in restored["by_stop"]))

    def test_scoring_a_range_saves_it_and_rerunning_replaces_it(self):
        snapshots.score(START, DAYS[-1])
        first = AccuracySnapshot.objects.get(range_start=START, range_end=DAYS[-1])
        self.assertEqual(first.payload["overall"]["queries"], self.result["overall"]["queries"])

        snapshots.score(START, DAYS[-1])

        self.assertEqual(AccuracySnapshot.objects.count(), 1)
        second = AccuracySnapshot.objects.get(range_start=START, range_end=DAYS[-1])
        self.assertEqual(second.payload, first.payload)
        self.assertGreaterEqual(second.computed_at, first.computed_at)
        self.assertIn("queries scored", snapshots.describe(second))
        # The run log says how often ours and the official ETA were on time (#156).
        self.assertRegex(snapshots.describe(second), r"; on time: ours \d+%, the Downtowner \d+%$")
        second.payload["overall"]["ours"].pop("on_time_percent")  # a score saved before #156
        second.payload["overall"]["official"].pop("on_time_percent")
        self.assertTrue(snapshots.describe(second).endswith("s"))  # just the time it took


class WindowsTests(SimpleTestCase):
    def test_a_normal_day_scores_the_default_week(self):
        tuesday = dt.date(2026, 11, 17)
        self.assertEqual(
            windows_for(tuesday),
            [(dt.date(2026, 11, 11), tuesday)],
        )

    def test_monday_also_scores_the_longest_range(self):
        monday = dt.date(2026, 11, 16)
        self.assertEqual(
            windows_for(monday),
            [(dt.date(2026, 11, 10), monday), (dt.date(2026, 10, 17), monday)],
        )


class ScoreAccuracyCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_history()

    def test_a_run_scores_the_default_week(self):
        out = io.StringIO()
        call_command("score_accuracy", stdout=out)

        today = timezone.localdate()
        AccuracySnapshot.objects.get(range_start=today - dt.timedelta(days=6), range_end=today)
        self.assertIn(f"{today - dt.timedelta(days=6)} to {today}", out.getvalue())

    def test_a_custom_range_is_scored_on_request(self):
        out = io.StringIO()
        call_command("score_accuracy", "--from=2026-11-02", "--to=2026-11-07", stdout=out)

        snapshot = AccuracySnapshot.objects.get(range_start=START, range_end=DAYS[-1])
        self.assertIn(f"{START} to {DAYS[-1]}: 20 queries scored", out.getvalue())
        self.assertGreater(snapshot.duration_seconds, 0)

    def test_a_backwards_range_is_refused(self):
        with self.assertRaises(CommandError):
            call_command("score_accuracy", "--from=2026-11-07", "--to=2026-11-02")
        self.assertEqual(AccuracySnapshot.objects.count(), 0)

    def test_an_oversized_range_is_refused(self):
        with self.assertRaises(CommandError):
            call_command("score_accuracy", "--from=2026-09-01", "--to=2026-11-07")
        self.assertEqual(AccuracySnapshot.objects.count(), 0)

    def test_half_a_range_is_refused(self):
        with self.assertRaises(CommandError):
            call_command("score_accuracy", "--from=2026-11-02")
        self.assertEqual(AccuracySnapshot.objects.count(), 0)


class ScoreAccuracyJobTests(SimpleTestCase):
    @mock.patch("history.jobs.call_command")
    def test_the_job_runs_the_command_and_returns_its_summary(self, call_command):
        def fake_score(*args, stdout, **kwargs):
            stdout.write("2026-11-11 to 2026-11-17: 412 queries scored in 38.0s")

        call_command.side_effect = fake_score
        summary = jobs.score_accuracy()
        call_command.assert_called_once()
        self.assertEqual(call_command.call_args.args, ("score_accuracy",))
        self.assertEqual(summary, "2026-11-11 to 2026-11-17: 412 queries scored in 38.0s")

    def test_the_job_is_on_the_nightly_schedule_after_the_import(self):
        self.assertEqual(all_jobs()["score_accuracy"].schedule, "53 10 * * *")
