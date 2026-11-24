"""The job clock (#40): starts each scheduled job's run on time.

GitHub starts scheduled workflow runs late, or not at all. In November 2026 the
daily jobs started 8 to 9 hours late, and the hourly line went 7 hours without
a single run: too late for hourly calendar refreshes, let alone trip reminders.
So .github/workflows/clock.yml runs this instead, the way downtowner-recorder
keeps its 5-minute polls on time. It stays up for hours, and in each minute
that a cron line in jobs.yml fires, it starts a "scheduled jobs" run with that
line as its `schedule` input, which runs the same jobs GitHub's schedule would.
It never touches the database.

    python3 jobs/clock.py --minutes 290    # what clock.yml runs
    python3 jobs/clock.py --dry-run        # print the runs instead of starting them

Standard library only, so the workflow needs no pip install.
"""

import argparse
import datetime as dt
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


def keep_time(lines, minutes, dry_run=False, now=utcnow, sleep=time.sleep):
    """In each minute that one of `lines` fires, start its jobs. Keep at it for
    at least `minutes`, then until no job is due soon, but never past LONGEST.
    Minutes go one by one, so a slow start delays the next minute's starts
    instead of skipping them. Returns how many runs couldn't be started."""
    began = now()
    least = dt.timedelta(minutes=minutes)
    minute = began.replace(second=0, microsecond=0) + ONE_MINUTE
    failures = 0
    while True:
        ran = minute - began
        if ran > LONGEST or (ran >= least and not due_soon(lines, minute)):
            print(f"Handing over to the next clock run before {minute:%H:%M} UTC", flush=True)
            return failures
        wait = (minute - now()).total_seconds()
        if wait > 0:
            sleep(wait)
        for line in due(lines, minute):
            started = start(line, dry_run)
            if not started:
                failures += 1
            outcome = "started" if started else "couldn't start"
            print(f"{minute:%Y-%m-%d %H:%M} UTC: {outcome} the jobs for {line}", flush=True)
        minute += ONE_MINUTE


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start each scheduled job's run on time (#40).")
    parser.add_argument("--minutes", type=int, default=290, help="keep time for at least this long")
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
    failures = keep_time(lines, args.minutes, args.dry_run)
    if failures:
        sys.exit(f"{failures} run(s) couldn't be started: see the warnings above")


if __name__ == "__main__":
    main()
