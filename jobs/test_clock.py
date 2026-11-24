"""Tests for the job clock (#40). Starting a run is mocked, so nothing here
reaches GitHub."""

import datetime as dt
import subprocess
from unittest import mock

import yaml
from django.conf import settings
from django.test import SimpleTestCase

from jobs import clock

WORKFLOWS = settings.BASE_DIR / ".github" / "workflows"
OUR_LINES = ["17 9 * * *", "29 10 * * *", "53 10 * * *", "41 * * * *"]


def utc(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)


def triggers(workflow):
    return workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true


class FakeTime:
    """Time that only moves when something sleeps, or a start takes a while."""

    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += dt.timedelta(seconds=seconds)


class CronTests(SimpleTestCase):
    def test_it_reads_the_lines_jobs_yml_starts(self):
        text = (WORKFLOWS / "jobs.yml").read_text()
        expected = [" ".join(entry["cron"].split()) for entry in triggers(yaml.safe_load(text))["schedule"]]
        self.assertEqual(clock.cron_lines(text), expected)
        self.assertEqual(clock.JOBS_WORKFLOW, WORKFLOWS / "jobs.yml")

    def test_our_lines_fire_when_their_comments_say(self):
        self.assertTrue(clock.matches("17 9 * * *", utc("2026-11-19T09:17")))
        self.assertFalse(clock.matches("17 9 * * *", utc("2026-11-19T09:18")))
        self.assertFalse(clock.matches("17 9 * * *", utc("2026-11-19T10:17")))
        for hour in range(24):
            self.assertTrue(clock.matches("41 * * * *", utc(f"2026-11-19T{hour:02}:41")))
        self.assertFalse(clock.matches("41 * * * *", utc("2026-11-19T09:40")))

    def test_steps_ranges_lists_and_weekdays(self):
        weekdays = "*/20 9-17 * * 1-5"  # every 20 minutes, 9 to 5 UTC, Monday to Friday
        self.assertTrue(clock.matches(weekdays, utc("2026-11-19T09:40")))  # a Thursday
        self.assertFalse(clock.matches(weekdays, utc("2026-11-19T09:50")))
        self.assertFalse(clock.matches(weekdays, utc("2026-11-19T18:00")))
        self.assertFalse(clock.matches(weekdays, utc("2026-11-22T09:40")))  # a Sunday
        self.assertTrue(clock.matches("0 0 * * 0", utc("2026-11-22T00:00")))
        self.assertTrue(clock.matches("0 0 * * 7", utc("2026-11-22T00:00")))
        self.assertTrue(clock.matches("5,35 * * * *", utc("2026-11-19T03:35")))
        self.assertTrue(clock.matches("5/30 * * * *", utc("2026-11-19T03:35")))
        # With both a day of the month and a day of the week, either will do, as in cron.
        self.assertTrue(clock.matches("0 0 1 * 1", utc("2026-11-23T00:00")))  # a Monday
        self.assertTrue(clock.matches("0 0 1 * 1", utc("2026-12-01T00:00")))  # the 1st
        self.assertFalse(clock.matches("0 0 1 * 1", utc("2026-11-24T00:00")))

    def test_it_refuses_lines_it_cant_read(self):
        for line in ["61 * * * *", "* * * *", "*/0 * * * *", "5-1 * * * *", "0 0 * * MON"]:
            with self.subTest(line=line), self.assertRaises(ValueError):
                clock.matches(line, utc("2026-11-19T00:00"))


class KeepTimeTests(SimpleTestCase):
    def keep_time(self, began, minutes, lines=OUR_LINES, started=True, start_takes=0):
        """Run the clock on fake time. Returns (minute, line) for each start,
        the time it handed over, and how many starts failed."""
        fake = FakeTime(utc(began))
        starts = []

        def start(line, dry_run=False):
            starts.append((f"{fake.now:%H:%M}", line))
            fake.sleep(start_takes)
            return started

        with mock.patch.object(clock, "start", side_effect=start), mock.patch("builtins.print"):
            failures = clock.keep_time(lines, minutes, now=fake, sleep=fake.sleep)
        return starts, fake.now, failures

    def test_it_starts_each_line_in_its_minute(self):
        starts, _, failures = self.keep_time("2026-11-19T08:30:10", minutes=60)
        self.assertEqual(
            starts,
            [("08:41", "41 * * * *"), ("09:17", "17 9 * * *"), ("09:41", "41 * * * *")],
        )
        self.assertEqual(failures, 0)

    def test_it_hands_over_only_when_no_job_is_due_soon(self):
        # It could stop at 10:21, but 10:29, 10:41 and 10:53 are coming, so it stays for them.
        starts, handed_over, _ = self.keep_time("2026-11-19T06:20:30", minutes=240)
        self.assertEqual(
            [minute for minute, _ in starts],
            ["06:41", "07:41", "08:41", "09:17", "09:41", "10:29", "10:41", "10:53"],
        )
        self.assertEqual(handed_over, utc("2026-11-19T10:53"))

    def test_it_never_runs_past_its_longest(self):
        _, handed_over, _ = self.keep_time("2026-11-19T00:00:30", minutes=1000)
        self.assertLessEqual(handed_over - utc("2026-11-19T00:00:30"), clock.LONGEST)

    def test_a_slow_start_delays_the_next_minute_but_never_skips_it(self):
        lines = ["17 9 * * *", "18 9 * * *"]
        starts, _, _ = self.keep_time("2026-11-19T09:10:00", minutes=5, lines=lines, start_takes=150)
        self.assertEqual(starts, [("09:17", "17 9 * * *"), ("09:19", "18 9 * * *")])

    def test_it_counts_the_runs_it_couldnt_start(self):
        _, _, failures = self.keep_time("2026-11-19T08:30:10", minutes=60, started=False)
        self.assertEqual(failures, 3)


@mock.patch("builtins.print")
@mock.patch.object(clock.time, "sleep")
@mock.patch.object(clock.subprocess, "run")
class StartTests(SimpleTestCase):
    def test_it_starts_a_scheduled_jobs_run_for_the_line(self, run, sleep, _print):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        self.assertTrue(clock.start("41 * * * *"))
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gh", "workflow", "run", "jobs.yml"])
        self.assertIn("schedule=41 * * * *", command)
        self.assertIn("environment=production", command)
        sleep.assert_not_called()

    def test_it_tries_three_times_then_warns(self, run, sleep, printed):
        run.return_value = subprocess.CompletedProcess([], 1, "", "HTTP 502")
        self.assertFalse(clock.start("41 * * * *"))
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(printed.call_args.args[0].startswith("::warning::"))

    def test_a_dry_run_starts_nothing(self, run, sleep, printed):
        self.assertTrue(clock.start("41 * * * *", dry_run=True))
        run.assert_not_called()


class WorkflowTests(SimpleTestCase):
    def test_the_clock_and_the_jobs_workflow_fit_together(self):
        jobs_yml = yaml.safe_load((WORKFLOWS / "jobs.yml").read_text())
        self.assertIn("schedule", triggers(jobs_yml)["workflow_dispatch"]["inputs"])
        clock_text = (WORKFLOWS / "clock.yml").read_text()
        clock_yml = yaml.safe_load(clock_text)
        self.assertEqual(clock_yml["permissions"]["actions"], "write")
        self.assertIn("python3 jobs/clock.py", clock_text)
        self.assertIn("gh workflow run clock.yml", clock_text)  # queues the next clock run
