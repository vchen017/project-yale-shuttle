"""Service alerts on the home page (#13). The Downtowner feed is mocked at
transit.downtowner.urlopen, so no test reaches the network."""

import datetime as dt
import json
from unittest import mock
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
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


def admin_form(title, message, starts_at, ends_at):
    """The admin's add/change form for an alert, with times entered in Eastern."""
    starts, ends = timezone.localtime(starts_at), timezone.localtime(ends_at)
    return {
        "title": title,
        "message": message,
        "starts_at_0": f"{starts:%Y-%m-%d}",
        "starts_at_1": f"{starts:%H:%M:%S}",
        "ends_at_0": f"{ends:%Y-%m-%d}",
        "ends_at_1": f"{ends:%H:%M:%S}",
    }


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class CustomAlertTests(TestCase):
    """Admins post, edit and remove custom alerts, and riders see each one on
    the home page only between its start and end (#20)."""

    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(
            "admin@example.com", is_staff=True, is_superuser=True
        )
        self.rider = Client()

    def home_alerts(self):
        cache.clear()
        return self.rider.get(reverse("home-alerts")).content.decode()

    def test_an_alert_shows_only_between_its_start_and_end(self, urlopen, now):
        urlopen.side_effect = lambda *args, **kwargs: feed_answer([])
        post_alert("Commencement road closures", NOW + dt.timedelta(hours=1), NOW + dt.timedelta(hours=3))

        self.assertNotIn("Commencement", self.home_alerts())
        self.assertNotIn("Commencement", self.rider.get("/").content.decode())
        now.return_value = NOW + dt.timedelta(hours=1)
        self.assertIn("Commencement", self.home_alerts())
        self.assertIn("Commencement", self.rider.get("/").content.decode())
        now.return_value = NOW + dt.timedelta(hours=3)
        self.assertNotIn("Commencement", self.home_alerts())

    def test_admins_edit_an_alert_and_riders_see_the_change(self, urlopen, now):
        urlopen.side_effect = lambda *args, **kwargs: feed_answer([])
        alert = post_alert("Football game", NOW - dt.timedelta(hours=1), NOW + dt.timedelta(hours=2))
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("admin:alerts_alert_change", args=[alert.pk]),
            admin_form("Football game: Chapel St closed", "Board at College & Elm instead.",
                       alert.starts_at, alert.ends_at),
        )

        self.assertEqual(response.status_code, 302)
        shown = self.home_alerts()
        self.assertIn("Football game: Chapel St closed", shown)
        self.assertIn("Board at College &amp; Elm instead.", shown)

    def test_admins_delete_an_alert(self, urlopen, now):
        urlopen.side_effect = lambda *args, **kwargs: feed_answer([])
        alert = post_alert("Football game", NOW - dt.timedelta(hours=1), NOW + dt.timedelta(hours=2))
        self.client.force_login(self.admin)

        response = self.client.post(reverse("admin:alerts_alert_delete", args=[alert.pk]), {"post": "yes"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Alert.objects.exists())
        self.assertNotIn("Football game", self.home_alerts())

    def test_end_now_takes_alerts_down_but_keeps_them(self, urlopen, now):
        urlopen.side_effect = lambda *args, **kwargs: feed_answer([])
        showing = post_alert("Showing", NOW - dt.timedelta(hours=1), NOW + dt.timedelta(hours=2))
        scheduled = post_alert("Scheduled", NOW + dt.timedelta(hours=1), NOW + dt.timedelta(hours=2))
        ended = post_alert("Ended", NOW - dt.timedelta(hours=3), NOW - dt.timedelta(hours=2))
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("admin:alerts_alert_changelist"),
            {"action": "end_now", "_selected_action": [showing.pk, scheduled.pk, ended.pk]},
            follow=True,
        )

        self.assertContains(response, "Ended 2 alerts.")
        showing.refresh_from_db()
        scheduled.refresh_from_db()
        ended.refresh_from_db()
        self.assertEqual((showing.starts_at, showing.ends_at), (NOW - dt.timedelta(hours=1), NOW))
        self.assertEqual((scheduled.starts_at, scheduled.ends_at), (NOW, NOW))
        self.assertEqual(ended.ends_at, NOW - dt.timedelta(hours=2))
        self.assertEqual(Alert.objects.count(), 3)
        now.return_value = NOW + dt.timedelta(hours=1, minutes=30)  # when "Scheduled" would have shown
        shown = self.home_alerts()
        self.assertNotIn("Showing", shown)
        self.assertNotIn("Scheduled", shown)

    def test_the_admin_list_shows_and_filters_by_status(self, urlopen, now):
        post_alert("Later", NOW + dt.timedelta(hours=1), NOW + dt.timedelta(hours=2))
        post_alert("Up now", NOW - dt.timedelta(hours=1), NOW + dt.timedelta(hours=2))
        post_alert("Over", NOW - dt.timedelta(hours=3), NOW - dt.timedelta(hours=2))
        self.client.force_login(self.admin)
        url = reverse("admin:alerts_alert_changelist")

        everything = self.client.get(url)
        for label in ["Scheduled", "Showing now", "Ended"]:
            self.assertContains(everything, f'<td class="field-status">{label}</td>')

        def titles(response):
            return [alert.title for alert in response.context["cl"].result_list]

        self.assertEqual(titles(self.client.get(url, {"status": "scheduled"})), ["Later"])
        self.assertEqual(titles(self.client.get(url, {"status": "showing"})), ["Up now"])
        self.assertEqual(titles(self.client.get(url, {"status": "ended"})), ["Over"])
        urlopen.assert_not_called()

    def test_the_form_says_times_are_eastern(self, urlopen, now):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("admin:alerts_alert_add")), "Eastern time.", count=2)

    def test_admins_get_a_post_an_alert_link_on_the_home_page_and_riders_dont(self, urlopen, now):
        link = f'href="{reverse("admin:alerts_alert_add")}"'
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/"), link)
        self.rider.force_login(get_user_model().objects.create_user("rider@example.com"))
        self.assertNotContains(self.rider.get("/"), link)
        urlopen.assert_not_called()

    def test_riders_cant_reach_the_alert_admin(self, urlopen, now):
        self.rider.force_login(get_user_model().objects.create_user("rider@example.com"))
        response = self.rider.get(reverse("admin:alerts_alert_add"))
        self.assertRedirects(response, f"/admin/login/?next={reverse('admin:alerts_alert_add')}")
