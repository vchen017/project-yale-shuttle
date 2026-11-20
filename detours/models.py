"""Detour detection's small tables (#55).

`Sample` keeps the raw position samples so detection survives a restart or a
Render sleep: the in-memory cache each process holds is wiped after 15 quiet
minutes, which is shorter than most detours (Vivian, #55 review). One row per
SAMPLE_SPACING, pruned to the horizon on every write, so the table holds at
most about an hour of page traffic. Written only when a page computes
detection — never by a scheduled job — so it cannot keep Neon awake by
itself; a page that computes detection reads the database anyway.

`UsualOffPath` is the nightly summary of when each route is routinely off its
drawn path (evening loops, the walk back to the garage): hours whose recorded
fixes are usually off it, which detection refuses to flag (see jobs.py).
"""

from django.db import models


class Sample(models.Model):
    """One moment's bus positions, folded into detection (#55)."""

    at = models.DateTimeField(unique=True)  # one sample at most per SAMPLE_SPACING
    # {route_id: {bus_id: off_the_path}} — the same shape record_sample's
    # callers see, so the flagging logic stays pure and testable.
    off_by_bus = models.JSONField()

    class Meta:
        ordering = ["at"]

    def __str__(self):
        return f"sample at {self.at:%H:%M}"


class UsualOffPath(models.Model):
    """The hours a route is routinely off its drawn path, from the recorded
    history (#55). Detection skips flagging a route in one of its usual
    hours — Purple's central-campus loop and Blue-Night's northern loop run
    most evenings, and that is the route's shape, not a detour."""

    route_id = models.IntegerField(primary_key=True)
    hours = models.JSONField()  # Eastern hours of day, e.g. [20, 21, 22]
    window_days = models.IntegerField()  # how much history the summary read
    updated_at = models.DateTimeField()

    def __str__(self):
        return f"route {self.route_id} usually off its path at {self.hours}"
