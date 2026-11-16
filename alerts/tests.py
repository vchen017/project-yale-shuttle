"""Service alerts on the home page (#13). The Downtowner feed is mocked at
transit.downtowner.urlopen, so no test reaches the network."""

import datetime as dt
import json
from unittest import mock
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from history.models import Announcement as RecordedAnnouncement
from history.models import Poll

from .models import Alert, FeedAnnouncement

NOW = dt.datetime(2026, 11, 16, 19, 0, tzinfo=dt.UTC)  # 2:00 PM Eastern

DETOUR = {
    "id": 23,
    "title": "Red, Brown",
    "message": "Due to Construction the State Street Station has been relocated to Chapel and Union.",
    "button_text": None,
    "button_url": None,
}
PURPLE = {
    "id": 25,
    "title": "Purple",
    "message": "Due to Mechanical issues, there is 2 Purple line running departing Cedar at :50 and :20",
    "button_text": None,
    "button_url": None,
}


def feed_answer(data):
    """What urlopen returns for one of the feed's JSON answers."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(data).encode()
    return response


def post_alert(title, starts_at, ends_at, message="A message."):
    return Alert.objects.create(title=title, message=message, starts_at=starts_at, ends_at=ends_at)


def record_poll(polled_at, announcement_id):
    """A poll in the imported history (#22) that had this announcement."""
    poll = Poll.objects.create(polled_at=polled_at)
    RecordedAnnouncement.objects.create(
        poll=poll, polled_at=polled_at, announcement_id=announcement_id, title="t", message="m"
    )


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class HomeAlertsTests(TestCase):
    url = reverse("home-alerts")

    def setUp(self):
        cache.clear()

    def test_feed_and_admin_alerts_show_newest_first(self, urlopen, now):
        record_poll(NOW - dt.timedelta(days=3), 23)  # the detour has been up for days
        post_alert("Shuttle stop moved", NOW - dt.timedelta(hours=1), NOW + dt.timedelta(days=1))
        urlopen.return_value = feed_answer([DETOUR, PURPLE])  # Purple is new: first seen now

        response = self.client.get(self.url)

        content = response.content.decode()
        positions = [content.index(text) for text in ["Purple", "Shuttle stop moved", "Red, Brown"]]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(response, DETOUR["message"])
        self.assertTrue(urlopen.call_args.args[0].full_url.endswith("/routes_announcements.php"))

    def test_each_alert_shows_its_posting_time_in_eastern_time(self, urlopen, now):
        record_poll(dt.datetime(2026, 11, 13, 0, 0, tzinfo=dt.UTC), 23)
        post_alert("Shuttle stop moved", NOW - dt.timedelta(hours=1), NOW + dt.timedelta(days=1))
        urlopen.return_value = feed_answer([DETOUR])

        response = self.client.get(self.url)

        self.assertContains(response, "Posted by Yale Shuttle")
        self.assertContains(response, "Mon, Nov 16, 1:00 PM")
        self.assertContains(response, "From the Downtowner, first seen")
        self.assertContains(response, "Thu, Nov 12, 7:00 PM")  # midnight UTC on Nov 13

    def test_expired_and_future_admin_alerts_are_hidden(self, urlopen, now):
        urlopen.return_value = feed_answer([])
        post_alert("Over", NOW - dt.timedelta(days=2), NOW - dt.timedelta(minutes=1))
        post_alert("Tomorrow", NOW + dt.timedelta(days=1), NOW + dt.timedelta(days=2))
        post_alert("Now", NOW - dt.timedelta(minutes=1), NOW + dt.timedelta(minutes=1))

        response = self.client.get(self.url)

        self.assertContains(response, "Now")
        self.assertNotContains(response, "Over")
        self.assertNotContains(response, "Tomorrow")

    def test_an_announcement_the_feed_dropped_is_hidden(self, urlopen, now):
        urlopen.return_value = feed_answer([DETOUR])
        self.client.get(self.url)
        cache.clear()
        urlopen.return_value = feed_answer([])

        self.assertNotContains(self.client.get(self.url), "Red, Brown")

    def test_admin_alerts_still_show_when_the_feed_is_down(self, urlopen, now):
        urlopen.side_effect = URLError("timed out")
        post_alert("Shuttle stop moved", NOW - dt.timedelta(hours=1), NOW + dt.timedelta(days=1))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shuttle stop moved")
        self.assertContains(response, "announcements are unavailable right now")

    def test_a_new_announcement_keeps_the_time_it_was_first_seen(self, urlopen, now):
        urlopen.side_effect = lambda *args, **kwargs: feed_answer([PURPLE])
        self.client.get(self.url)
        cache.clear()
        now.return_value = NOW + dt.timedelta(hours=2)

        response = self.client.get(self.url)

        self.assertEqual(FeedAnnouncement.objects.get().first_seen_at, NOW)
        self.assertContains(response, "Mon, Nov 16, 2:00 PM")

    def test_unreadable_announcements_are_skipped_and_only_web_links_kept(self, urlopen, now):
        urlopen.return_value = feed_answer([
            "not an announcement",
            {"title": "No id"},
            {"id": 40, "title": "", "message": "  "},
            {"id": 41, "title": "Link", "message": "m", "button_text": "Bad", "button_url": "javascript:alert(1)"},
            {"id": 42, "title": "Map", "message": "m", "button_text": "See the map", "button_url": "https://example.com/map"},
        ])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "No id")
        self.assertNotContains(response, "javascript:")
        self.assertContains(response, '<a class="alert-link" href="https://example.com/map"')
        self.assertEqual(sorted(FeedAnnouncement.objects.values_list("announcement_id", flat=True)), [41, 42])

    def test_feed_text_is_escaped(self, urlopen, now):
        urlopen.return_value = feed_answer([{"id": 50, "title": "<script>x</script>", "message": "m"}])
        response = self.client.get(self.url)
        self.assertNotContains(response, "<script>x</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_no_alerts_gives_an_empty_answer(self, urlopen, now):
        urlopen.return_value = feed_answer([])
        self.assertEqual(self.client.get(self.url).content.decode().strip(), "")


@mock.patch("transit.downtowner.urlopen")
class HomePageTests(TestCase):
    def test_home_page_shows_admin_alerts_without_waiting_on_the_feed(self, urlopen):
        now = timezone.now()
        post_alert("Shuttle stop moved", now - dt.timedelta(hours=1), now + dt.timedelta(hours=1))

        response = self.client.get("/")

        urlopen.assert_not_called()
        self.assertContains(response, "Shuttle stop moved")
        self.assertContains(response, f'data-alerts-url="{reverse("home-alerts")}"')
        self.assertContains(response, "core/alerts.js")

    def test_alerts_section_is_hidden_until_there_is_an_alert(self, urlopen):
        response = self.client.get("/")
        self.assertContains(response, 'id="service-alerts"')
        self.assertContains(response, " hidden>")
        urlopen.assert_not_called()


class AlertModelTests(TestCase):
    def test_an_alert_has_to_end_after_it_starts(self):
        now = timezone.now()
        alert = Alert(title="t", message="m", starts_at=now, ends_at=now - dt.timedelta(minutes=1))
        with self.assertRaises(ValidationError):
            alert.full_clean()

    def test_admins_post_alerts_in_the_admin(self):
        admin = get_user_model().objects.create_user("admin@example.com", is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin:alerts_alert_add"),
            {
                "title": "No Blue line tonight",
                "message": "The evening Blue isn't running.",
                "starts_at_0": "2026-11-16",
                "starts_at_1": "17:00:00",
                "ends_at_0": "2026-11-17",
                "ends_at_1": "02:00:00",
            },
        )
        self.assertEqual(response.status_code, 302)
        alert = Alert.objects.get()
        # Entered in Eastern time, stored in UTC.
        self.assertEqual(alert.starts_at, dt.datetime(2026, 11, 16, 22, 0, tzinfo=dt.UTC))
