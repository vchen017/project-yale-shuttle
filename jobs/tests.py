import datetime as dt
from io import StringIO
from unittest import mock

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from jobs import registry, runner
from jobs.models import JobRun
from jobs.registry import Job


def make_job(func, name="sample", schedule="5 4 * * *", timeout=dt.timedelta(minutes=10)):
    return Job(name=name, func=func, schedule=schedule, timeout=timeout, description="A test job.")


def run_quietly(test, job, trigger=""):
    """runner.run, with its log lines captured instead of printed."""
    with test.assertLogs("jobs.runner", "INFO"):
        return runner.run(job, trigger)


class RunnerTests(TestCase):
    def test_a_successful_run_is_recorded_with_its_summary(self):
        run = run_quietly(self, make_job(lambda: "3 polls imported"), trigger="schedule")
        run.refresh_from_db()
        self.assertEqual(run.status, JobRun.Status.SUCCEEDED)
        self.assertEqual(run.output, "3 polls imported")
        self.assertEqual(run.trigger, "schedule")
        self.assertGreaterEqual(run.finished_at, run.started_at)

    def test_a_failing_job_becomes_a_failed_run_with_the_error(self):
        def broken():
            raise RuntimeError("GitHub said no")

        run = run_quietly(self, make_job(broken))
        run.refresh_from_db()
        self.assertEqual(run.status, JobRun.Status.FAILED)
        self.assertIn("RuntimeError: GitHub said no", run.output)
        self.assertIsNotNone(run.finished_at)

    def test_a_job_never_runs_twice_at_once(self):
        calls = []
        JobRun.objects.create(job="sample")  # a run of the same job, still going
        result = run_quietly(self, make_job(lambda: calls.append("ran")))
        self.assertIsNone(result)
        self.assertEqual(calls, [])
        self.assertEqual(JobRun.objects.count(), 1)

    def test_another_jobs_run_doesnt_block_this_one(self):
        JobRun.objects.create(job="other")
        self.assertIsNotNone(run_quietly(self, make_job(lambda: None)))

    def test_a_run_that_died_stops_blocking_after_its_timeout(self):
        dead = JobRun.objects.create(job="sample", started_at=timezone.now() - dt.timedelta(hours=1))
        run = run_quietly(self, make_job(lambda: "ok", timeout=dt.timedelta(minutes=10)))
        self.assertEqual(run.status, JobRun.Status.SUCCEEDED)
        dead.refresh_from_db()
        self.assertEqual(dead.status, JobRun.Status.FAILED)
        self.assertEqual(dead.output, runner.ABANDONED)

    def test_a_long_output_keeps_its_end(self):
        run = run_quietly(self, make_job(lambda: "x" * 20_000 + "the error"))
        self.assertLessEqual(len(run.output), runner.OUTPUT_LIMIT)
        self.assertTrue(run.output.endswith("the error"))


class RunJobCommandTests(TestCase):
    def setUp(self):
        self.calls = []

        def first():
            self.calls.append("first")
            return "did the first thing"

        def second():
            self.calls.append("second")
            raise OSError("no network")

        def hourly():
            self.calls.append("hourly")

        self.jobs = {
            "first": make_job(first, name="first", schedule="5 4 * * *"),
            "second": make_job(second, name="second", schedule="5 4 * * *"),
            "hourly": make_job(hourly, name="hourly", schedule="40 * * * *"),
        }
        patcher = mock.patch.object(registry, "all_jobs", return_value=self.jobs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_job(self, *args):
        out, err = StringIO(), StringIO()
        with self.assertLogs("jobs.runner", "INFO"):
            call_command("run_job", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_runs_the_named_jobs_only(self):
        out, _ = self.run_job("first", "--trigger", "Actions, by vchen017")
        self.assertEqual(self.calls, ["first"])
        self.assertIn("first: succeeded", out)
        self.assertIn("did the first thing", out)
        self.assertEqual(JobRun.objects.get().trigger, "Actions, by vchen017")

    def test_a_schedule_runs_its_jobs_and_one_failure_doesnt_stop_the_rest(self):
        with self.assertRaisesMessage(CommandError, "Failed: second"):
            self.run_job("--schedule", "5  4 * * *")  # extra spaces are fine
        self.assertEqual(sorted(self.calls), ["first", "second"])
        statuses = dict(JobRun.objects.values_list("job", "status"))
        self.assertEqual(statuses, {"first": "succeeded", "second": "failed"})

    def test_a_job_already_running_is_skipped_without_failing(self):
        JobRun.objects.create(job="first")
        out, _ = self.run_job("first")
        self.assertEqual(self.calls, [])
        self.assertIn("first: skipped", out)

    def test_bad_requests_fail_before_anything_runs(self):
        for args, message in [
            (("first", "nope"), "Unknown job: nope"),
            (("--schedule", "1 2 3 4 5"), "No job runs on the schedule"),
            ((), "Name the jobs to run, or give --schedule"),
            (("first", "--schedule", "5 4 * * *"), "but not both"),
        ]:
            with self.subTest(args=args), self.assertRaisesMessage(CommandError, message):
                call_command("run_job", *args, stdout=StringIO())
        self.assertEqual(self.calls, [])
        self.assertFalse(JobRun.objects.exists())

    def test_refuses_to_run_new_code_against_an_old_schema(self):
        with mock.patch(
            "jobs.management.commands.run_job.unapplied_migrations",
            return_value=["history.0004_newer"],
        ):
            with self.assertRaisesMessage(CommandError, "missing 1 migration(s)"):
                call_command("run_job", "first", stdout=StringIO())
        self.assertEqual(self.calls, [])
        self.assertFalse(JobRun.objects.exists())

    def test_lists_the_jobs_and_their_schedules(self):
        out = StringIO()
        call_command("run_job", "--list", stdout=out)
        self.assertIn("hourly  [40 * * * * UTC]  A test job.", out.getvalue())
        self.assertEqual(self.calls, [])


def _a_job():
    pass


def _another_job():
    pass


class RegistryTests(SimpleTestCase):
    def test_finds_each_apps_jobs(self):
        self.assertIn("import_history", registry.all_jobs())

    def test_two_jobs_cant_share_a_name(self):
        with mock.patch.dict(registry._jobs):
            registry.job(schedule="1 1 * * *", name="twin")(_a_job)
            with self.assertRaisesMessage(ValueError, "Two jobs are called 'twin'"):
                registry.job(schedule="1 1 * * *", name="twin")(_another_job)

    def test_the_workflow_starts_every_job_and_nothing_else(self):
        """Each job's schedule is a cron line in jobs.yml, and each line starts a job."""
        path = settings.BASE_DIR / ".github" / "workflows" / "jobs.yml"
        workflow = yaml.safe_load(path.read_text())
        triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
        crons = {" ".join(entry["cron"].split()) for entry in triggers["schedule"]}
        schedules = {job.schedule for job in registry.all_jobs().values()}
        self.assertEqual(schedules - crons, set(), "jobs whose schedule jobs.yml never starts")
        self.assertEqual(crons - schedules, set(), "cron lines in jobs.yml that start no job")


class AdminTests(TestCase):
    def setUp(self):
        admin = get_user_model().objects.create_superuser(
            "admin@example.com", "admin@example.com", "a-test-password-1"
        )
        self.client.force_login(admin)

    def test_admins_see_the_run_log_newest_first_but_cant_edit_it(self):
        now = timezone.now()
        JobRun.objects.create(
            job="import_history",
            status=JobRun.Status.SUCCEEDED,
            started_at=now - dt.timedelta(days=1),
            finished_at=now - dt.timedelta(days=1) + dt.timedelta(seconds=75),
            output="2026-11-13: 288 polls imported",
        )
        failed = JobRun.objects.create(
            job="import_history",
            status=JobRun.Status.FAILED,
            started_at=now,
            finished_at=now + dt.timedelta(seconds=4),
            output="Traceback (most recent call last):\n  ...\nOSError: no network",
        )
        page = self.client.get(reverse("admin:jobs_jobrun_changelist"))
        self.assertContains(page, "1m 15s")
        self.assertContains(page, "OSError: no network")  # a failure's summary is its error
        html = page.content.decode()
        self.assertLess(html.index("OSError: no network"), html.index("288 polls imported"))

        with self.assertLogs("django.request", "WARNING"):  # Django logs every 403
            self.assertEqual(self.client.get(reverse("admin:jobs_jobrun_add")).status_code, 403)
        detail = self.client.get(reverse("admin:jobs_jobrun_change", args=[failed.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, 'name="_save"')
