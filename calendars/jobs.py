"""The calendars app's scheduled jobs (#25)."""


from jobs.registry import job

from . import demo, services


@job(schedule="41 * * * *")
def refresh_calendars():
    """Refresh riders' ICS calendars (#25): hourly, re-read each connected
    address and replace its events. Also re-anchors the demo events (#8)
    whose time has passed, so the when-to-leave page shows graders something
    even hours after the last deploy."""
    moved = demo.reanchor()
    return f"{services.refresh_all()}; {moved} demo events re-anchored"
