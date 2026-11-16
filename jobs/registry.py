"""The list of scheduled jobs. Each app declares its own in `<app>/jobs.py` (#40).

    import datetime as dt

    from jobs.registry import job

    @job(schedule="17 9 * * *", timeout=dt.timedelta(minutes=20))
    def import_history():
        '''Load the recorder's latest shuttle history (#22).'''
        ...
        return "a short summary for the run log"

`schedule` is a cron line in UTC. The same line must be in
.github/workflows/jobs.yml, which is what starts the jobs; a test checks that
every job's line is there. The docstring's first line describes the job in
`run_job --list`.

A job must be safe to run late, twice, or after a skipped run: GitHub can start
a scheduled run late, or drop one when it's busy. And mind the database: Neon
stays awake for 5 minutes after each use, and its free plan has 100 compute
hours a month for staging and production together, so a job that touches the
database more often than hourly needs a plan for that first (see AGENTS.md).
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from django.utils.module_loading import autodiscover_modules


@dataclass(frozen=True)
class Job:
    name: str
    func: Callable[[], object]
    schedule: str
    timeout: dt.timedelta
    description: str


_jobs = {}
_discovered = False


def job(*, schedule, timeout=dt.timedelta(minutes=10), name=None):
    """Register the decorated function as a scheduled job.

    `timeout` is how long a run may take. A run still unfinished after that is
    taken to have died with its process, and stops blocking the next run.
    """

    def register(func):
        job_name = name or func.__name__
        where = f"{func.__module__}.{func.__qualname__}"
        existing = _jobs.get(job_name)
        if existing and f"{existing.func.__module__}.{existing.func.__qualname__}" != where:
            raise ValueError(f"Two jobs are called {job_name!r}")
        description = (func.__doc__ or "").strip().split("\n")[0]
        _jobs[job_name] = Job(job_name, func, " ".join(schedule.split()), timeout, description)
        return func

    return register


def all_jobs():
    """Every job by name, once each installed app's jobs.py has been imported."""
    global _discovered
    if not _discovered:
        autodiscover_modules("jobs")
        _discovered = True
    return dict(_jobs)
