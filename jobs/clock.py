"""The job clock (#40): starts each scheduled job's run on time.

GitHub starts scheduled workflow runs late, or not at all. In November 2026 the
daily jobs started 8 to 9 hours late, and the hourly line went 7 hours without
a single run: too late for hourly calendar refreshes, let alone trip reminders.
So .github/workflows/clock.yml runs this instead, the way downtowner-recorder
keeps its 5-minute polls on time. It stays up for hours, and in each minute
that a cron line in jobs.yml fires, it starts a "scheduled jobs" run with that
line as its `schedule` input, which runs the same jobs GitHub's schedule would.
It never touches the database.

Each run hands the next one the first minute it didn't keep, and the next run
starts from there: anything that came due while it waited to start, it starts
at once, late, instead of skipping it (#189).

    python3 jobs/clock.py --minutes 290 --since 2026-11-27T22:46Z    # what clock.yml runs
    python3 jobs/clock.py --dry-run        # print the runs instead of starting them

Standard library only, so the workflow needs no pip install.
"""

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import time
from pathlib import Path

JOBS_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "jobs.yml"
ONE_MINUTE = dt.timedelta(minutes=1)
# Hand over to the next clock run only when no job is due this soon, so none
# comes due while one clock run is ending and the next is starting.
QUIET = dt.timedelta(minutes=15)
# Stop by then, whatever is due: clock.yml gives a run 355 minutes.
LONGEST = dt.timedelta(minutes=340)
# A run that starts late looks back this far for what came due meanwhile.
# Older starts are left to GitHub's own schedule, whose runs do the jobs once
# no run the clock started has succeeded for two hours (jobs.yml).
CATCH_UP = dt.timedelta(hours=2)
# Each cron field's name and range. In the day of the week, 0 and 7 are Sunday.
FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31), ("month", 1, 12), ("weekday", 0, 7))


def cron_lines(workflow_text):
    """The schedule's cron lines in a workflow file, spaced the way the job
    registry spaces them."""
    found = re.findall(r"""^\s*-\s*cron:\s*["']([^"']+)["']""", workflow_text, re.MULTILINE)
    return [" ".join(line.split()) for line in found]


def _allowed(field, name, low, high):
    """The numbers a cron field allows: `*`, `5`, `1-5`, `*/15`, `0-30/10`,
    `5/15`, or a comma list of those."""
    allowed = set()
    for part in field.split(","):
        values, _, step = part.partition("/")
        if values == "*":
            first, last = low, high
        elif "-" in values:
            first, last = (int(number) for number in values.split("-", 1))
        else:
            first = int(values)
            last = high if step else first
        every = int(step) if step else 1
        if not low <= first <= last <= high or every < 1:
            raise ValueError(f"cron {name} {field!r} is outside {low}-{high}")
        allowed.update(range(first, last + 1, every))
    return allowed


def matches(line, when):
    """Whether the cron line `line` (UTC) fires in `when`'s minute."""
    fields = line.split()
    if len(fields) != 5:
        raise ValueError(f"not a five-field cron line: {line!r}")
    minute, hour, day, month, weekday = (
        _allowed(field, name, low, high) for field, (name, low, high) in zip(fields, FIELDS)
    )
    if 7 in weekday:
        weekday.add(0)
    on_day = when.day in day
    on_weekday = when.isoweekday() % 7 in weekday  # Sunday is 0
    if fields[2] != "*" and fields[4] != "*":
        on_date = on_day or on_weekday  # cron's rule: with both limited, either will do
    else:
        on_date = on_day and on_weekday
    return when.minute in minute and when.hour in hour and when.month in month and on_date


def due(lines, minute):
    """The lines that fire in this minute."""
    return [line for line in lines if matches(line, minute)]


def due_soon(lines, minute, within=QUIET):
    """Whether any line fires in this minute or the ones after it, up to `within`."""
    return any(due(lines, minute + n * ONE_MINUTE) for n in range(int(within / ONE_MINUTE)))


def start(line, dry_run=False, tries=3):
    """Start a "scheduled jobs" run on production for a cron line. True once
    GitHub takes it; False after `tries` failures, with a warning in the log."""
    command = [
        "gh", "workflow", "run", "jobs.yml", "--ref", "main",
        "-f", "environment=production", "-f", f"schedule={line}",
    ]
    if dry_run:
        print("Would run:", " ".join(command), flush=True)
        return True
    for attempt in range(tries):
        if attempt:
            time.sleep(10)
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            return True
    print(f"::warning::Couldn't start the jobs for {line}: {result.stderr.strip()}", flush=True)
    return False


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def missed(lines, since, first):
    """The lines that came due from `since` until `first`: between the last
    clock run's handover and this run's first minute. Each line once, with the
    last minute it came due, oldest first. None without a `since`."""
    if since is None:
        return []
    last_due = {}
    minute = max(since, first - CATCH_UP)
    while minute < first:
        for line in due(lines, minute):
            last_due[line] = minute
        minute += ONE_MINUTE
    return sorted(last_due.items(), key=lambda item: item[1])


def _start(line, dry_run, at, came_due=None):
    """Start a line's jobs and say so in the log; True if GitHub took it."""
    started = start(line, dry_run)
    outcome = "started" if started else "couldn't start"
    late = f", late: it came due at {came_due:%H:%M}" if came_due else ""
    print(f"{at:%Y-%m-%d %H:%M} UTC: {outcome} the jobs for {line}{late}", flush=True)
    return started


def keep_time(lines, minutes, dry_run=False, now=utcnow, sleep=time.sleep, since=None):
    """In each minute that one of `lines` fires, start its jobs. Keep at it for
    at least `minutes`, then until no job is due soon, but never past LONGEST.
    Minutes go one by one, so a slow start delays the next minute's starts
    instead of skipping them.

    `since` is the first minute the last clock run didn't keep. What came due
    from then until this run's first minute starts at once, late, each line
    once (#189). Without it, as when GitHub's schedule starts the clock, this
    run keeps time from the next minute.

    Returns how many runs couldn't be started, and the first minute this run
    didn't keep: the next run's `since`."""
    began = now()
    least = dt.timedelta(minutes=minutes)
    minute = began.replace(second=0, microsecond=0) + ONE_MINUTE
    failures = 0
    for line, came_due in missed(lines, since, minute):
        if not _start(line, dry_run, now(), came_due):
            failures += 1
    while True:
        ran = minute - began
        if ran > LONGEST or (ran >= least and not due_soon(lines, minute)):
            print(f"Handing over to the next clock run before {minute:%H:%M} UTC", flush=True)
            return failures, minute
        wait = (minute - now()).total_seconds()
        if wait > 0:
            sleep(wait)
        for line in due(lines, minute):
            if not _start(line, dry_run, minute):
                failures += 1
        minute += ONE_MINUTE


def parse_minute(text):
    """A minute as clock.yml hands it on, "2026-11-27T22:46Z", in UTC. None
    when it's blank, or isn't one, so the clock still keeps time."""
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        print(f"::warning::{text!r} isn't a minute like 2026-11-27T22:46Z: keeping time from the next one", flush=True)
        return None


def hand_on(name, value):
    """Give the workflow's later steps a value, when a workflow runs this."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as output:
            output.write(f"{name}={value}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start each scheduled job's run on time (#40).")
    parser.add_argument("--minutes", type=int, default=290, help="keep time for at least this long")
    parser.add_argument(
        "--since", default="",
        help="the first minute the last clock run didn't keep, like 2026-11-27T22:46Z (#189)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the runs instead of starting them"
    )
    args = parser.parse_args(argv)
    lines = cron_lines(JOBS_WORKFLOW.read_text())
    if not lines:
        sys.exit(f"No cron lines in {JOBS_WORKFLOW}")
    for line in lines:
        matches(line, utcnow())  # a line it can't read stops it now, not hours in
    print(f"Keeping time for: {', '.join(lines)}", flush=True)
    failures, handover = keep_time(lines, args.minutes, args.dry_run, since=parse_minute(args.since))
    hand_on("handover", f"{handover:%Y-%m-%dT%H:%MZ}")  # the next clock run's --since
    if failures:
        sys.exit(f"{failures} run(s) couldn't be started: see the warnings above")


if __name__ == "__main__":
    main()
