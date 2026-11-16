"""The service alerts riders see on the home page (#13).

Two sources, merged newest first:

- the alerts our admins post in /admin/ (Alert), shown from their start until
  their end;
- the Downtowner's announcements, from transit.downtowner.announcements()
  (#36). The feed lists only the ones in effect, so an ended announcement
  disappears on its own. If the feed is down, the admin alerts still show.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Min
from django.utils import timezone

from history.models import Announcement as RecordedAnnouncement
from transit import downtowner

from .models import Alert, FeedAnnouncement

ADMIN = "admin"
DOWNTOWNER = "downtowner"


@dataclass(frozen=True)
class ShownAlert:
    """One alert as a rider sees it, from either source."""

    source: str  # ADMIN or DOWNTOWNER
    title: str
    message: str
    posted_at: datetime
    link_text: str = ""
    link_url: str = ""
    # Breaks ties between alerts posted at the same moment: higher is newer.
    order: int = 0


def admin_alerts(now=None):
    """The admins' alerts in effect now, newest first. Reads only the database."""
    return [
        ShownAlert(ADMIN, alert.title, alert.message, alert.starts_at, order=alert.pk)
        for alert in Alert.objects.current(now).order_by("-starts_at", "-pk")
    ]


def current_alerts():
    """Every alert in effect now, newest first, and whether the feed answered.

    Returns (alerts, feed_ok). When the feed is down, feed_ok is False and the
    admins' alerts are still there.
    """
    now = timezone.now()
    alerts = admin_alerts(now)
    try:
        announcements = downtowner.announcements()
    except downtowner.FeedUnavailable:
        feed_ok = False
    else:
        feed_ok = True
        alerts += feed_alerts(announcements, now)
    alerts.sort(key=lambda alert: (alert.posted_at, alert.order), reverse=True)
    return alerts, feed_ok


def feed_alerts(announcements, now):
    """The feed's announcements as ShownAlerts, each with when we first saw it.

    Entries we can't read (no id, or no title and no message) are left out.
    """
    parsed = {}
    for item in announcements if isinstance(announcements, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            announcement_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        title = _text(item.get("title"))
        message = _text(item.get("message"))
        if title or message:
            parsed[announcement_id] = item | {"title": title, "message": message}
    if not parsed:
        return []

    first_seen = _first_seen(set(parsed), now)
    alerts = []
    for announcement_id, item in parsed.items():
        link_url = _text(item.get("button_url"))
        if not link_url.startswith(("https://", "http://")):
            link_url = ""
        alerts.append(
            ShownAlert(
                DOWNTOWNER,
                item["title"],
                item["message"],
                first_seen[announcement_id],
                link_text=_text(item.get("button_text")) if link_url else "",
                link_url=link_url,
                order=announcement_id,
            )
        )
    return alerts


def _first_seen(ids, now):
    """{announcement id: when we first saw it}, recording the ones that are new.

    A new announcement's time is its earliest poll in the recorded history
    (#22) when the import has reached it, and otherwise now. It's stored once
    and never moves, so two requests at once can't change what riders see.
    """
    seen = dict(
        FeedAnnouncement.objects.filter(announcement_id__in=ids).values_list(
            "announcement_id", "first_seen_at"
        )
    )
    new = ids - seen.keys()
    if new:
        recorded = dict(
            RecordedAnnouncement.objects.filter(announcement_id__in=new)
            .values("announcement_id")
            .annotate(first=Min("polled_at"))
            .values_list("announcement_id", "first")
        )
        FeedAnnouncement.objects.bulk_create(
            [
                FeedAnnouncement(announcement_id=i, first_seen_at=recorded.get(i, now))
                for i in new
            ],
            ignore_conflicts=True,
        )
        seen |= dict(
            FeedAnnouncement.objects.filter(announcement_id__in=new).values_list(
                "announcement_id", "first_seen_at"
            )
        )
    return seen


def _text(value):
    return value.strip() if isinstance(value, str) else ""
