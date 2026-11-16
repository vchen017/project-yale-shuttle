"""The history app's scheduled jobs (see jobs/registry.py, #40)."""

import datetime as dt
from io import StringIO

from django.core.management import call_command

from jobs.registry import job

BACKFILL_DAYS = 5  # about 4 minutes each, so a run stays well inside its timeout


@job(schedule="17 9 * * *", timeout=dt.timedelta(minutes=45))
def import_history():
    """Load the recorder's latest shuttle history (#22): daily, 9:17 UTC (4:17 AM Eastern)."""
    # The last three recorded UTC days: yesterday in full, today so far, and a
    # spare day in case GitHub started a run late or skipped one. Polls that are
    # already imported are skipped, so rerunning it adds nothing twice.
    # It also fills in up to BACKFILL_DAYS older days the predictions learn
    # from (the last four weeks) that aren't loaded yet: a new database, or a
    # run that failed. Run it again by hand to fill faster.
    output = StringIO()
    call_command("import_history", days=3, backfill=BACKFILL_DAYS, stdout=output)
    return output.getvalue().strip()
