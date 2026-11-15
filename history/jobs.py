"""The history app's scheduled jobs (see jobs/registry.py, #40)."""

import datetime as dt
from io import StringIO

from django.core.management import call_command

from jobs.registry import job


@job(schedule="17 9 * * *", timeout=dt.timedelta(minutes=45))
def import_history():
    """Load the recorder's latest shuttle history (#22): daily, 9:17 UTC (4:17 AM Eastern)."""
    # The last three recorded UTC days: yesterday in full, today so far, and a
    # spare day in case GitHub started a run late or skipped one. Polls that are
    # already imported are skipped, so rerunning it adds nothing twice.
    output = StringIO()
    call_command("import_history", days=3, stdout=output)
    return output.getvalue().strip()
