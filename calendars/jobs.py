"""The calendars app's scheduled jobs (#25)."""


from jobs.registry import job

from . import services


@job(schedule="41 * * * *")
def refresh_calendars():
    """Refresh riders' ICS calendars (#25): hourly, re-read each connected
    address and replace its events."""
    return services.refresh_all()
