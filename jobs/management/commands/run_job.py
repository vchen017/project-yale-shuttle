"""Run scheduled jobs now (#40).

    python manage.py run_job --list                    # every job and its schedule
    python manage.py run_job import_history            # run these jobs now
    python manage.py run_job --schedule "17 9 * * *"   # the jobs that cron line starts

.github/workflows/jobs.yml runs the --schedule form, started on time by the job
clock (jobs/clock.py). Every run is recorded in the run log (/admin/ →
Scheduled jobs). The command fails if any job failed, so the workflow run
shows it.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from jobs import registry, runner
from jobs.models import JobRun


def unapplied_migrations():
    """Migrations this code has that the database hasn't applied, as "app.name"."""
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{migration.app_label}.{migration.name}" for migration, _backwards in plan]


class Command(BaseCommand):
    help = "Run scheduled jobs now: by name, or every job that a cron schedule starts."

    def add_arguments(self, parser):
        parser.add_argument("names", nargs="*", help="The jobs to run.")
        parser.add_argument(
            "--schedule", help="Run every job with this cron schedule (UTC), as the workflow does."
        )
        parser.add_argument(
            "--list", action="store_true", dest="list_jobs", help="List the jobs and their schedules."
        )
        parser.add_argument(
            "--trigger", default="by hand", help="What started this run, for the run log."
        )

    def handle(self, *args, names, schedule, list_jobs, trigger, **options):
        jobs = registry.all_jobs()
        if list_jobs:
            for job in sorted(jobs.values(), key=lambda job: job.name):
                self.stdout.write(f"{job.name}  [{job.schedule} UTC]  {job.description}")
            return

        chosen = self._chosen(jobs, names, schedule)
        missing = unapplied_migrations()
        if missing:
            raise CommandError(
                f"This database is missing {len(missing)} migration(s) this code has, starting "
                f"with {missing[0]}. Deploy or promote this code first, so they run: jobs "
                "don't run new code against an old schema."
            )

        failed = []
        for job in chosen:
            run = runner.run(job, trigger)
            if run is None:
                self.stdout.write(f"{job.name}: skipped, because another run of it is still going")
                continue
            took = f"{run.duration.total_seconds():.0f}s"
            if run.status == JobRun.Status.FAILED:
                failed.append(job.name)
                self.stderr.write(f"{job.name}: failed after {took}\n{run.output}")
            else:
                self.stdout.write(f"{job.name}: succeeded in {took}")
                if run.output:
                    self.stdout.write(run.output)

        if failed:
            raise CommandError(f"Failed: {', '.join(failed)}. See the run log in /admin/.")

    def _chosen(self, jobs, names, schedule):
        """The jobs to run, checked before any of them starts."""
        if bool(names) == bool(schedule):
            raise CommandError("Name the jobs to run, or give --schedule, but not both.")
        if schedule:
            wanted = " ".join(schedule.split())
            chosen = [job for job in jobs.values() if job.schedule == wanted]
            if not chosen:
                raise CommandError(f"No job runs on the schedule {schedule!r}.")
            return chosen
        unknown = [name for name in names if name not in jobs]
        if unknown:
            known = ", ".join(sorted(jobs)) or "none"
            raise CommandError(f"Unknown job: {', '.join(unknown)}. The jobs are: {known}.")
        return [jobs[name] for name in names]
