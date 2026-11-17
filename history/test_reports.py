"""Missed-bus reports (#6). The Downtowner feed is mocked at
transit.downtowner.urlopen, answering by endpoint, so no test reaches the
network; `timezone.now` is frozen where times matter."""

import datetime as dt
import json
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import RiderReport

ET = ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 11, 18, 14, 40, tzinfo=ET)  # a Wednesday, 2:40 PM Eastern
Kind = RiderReport.Kind

ROUTES = [
    {"id": 1, "name": "Blue - Weekday Daytime", "short_name": "BD", "active": True, "stops": [10, 12]},
    {"id": 15, "name": "Gold Route", "short_name": "G", "active": True, "stops": [10]},
    {"id": 16, "name": "Blue West", "short_name": "BW", "active": False, "stops": [10]},
]
STOPS = [
    {"id": 10, "name": "Science Hill", "lat": 41.316, "lon": -72.922, "description": ""},
    {"id": 12, "name": "Phelps Gate", "lat": 41.311, "lon": -72.924, "description": ""},
]


def feed(routes=ROUTES):
    """A urlopen stand-in that answers the routes and stops feeds."""
    answers = {"/routes_routes.php": routes, "/routes_stops.php": STOPS}

    def urlopen(request, timeout):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(answers[urlsplit(request.full_url).path]).encode()
        return response

    return urlopen


def at(**offset):
    return NOW - dt.timedelta(**offset)


class ReportsMixin:
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.rider = User.objects.create_user("rider@example.com", password="a-rider-password-1")
        self.other = User.objects.create_user("other@example.com")

    def add(self, route_id, stop_id, kind, when, user=None, is_sample=False):
        return RiderReport.objects.create(
            user=user or self.other,
            route_id=route_id,
            route_name={1: "Blue - Weekday Daytime", 15: "Gold Route", 16: "Blue West"}[route_id],
            stop_id=stop_id,
            stop_name={10: "Science Hill", 12: "Phelps Gate"}[stop_id],
            kind=kind,
            created_at=when,
            is_sample=is_sample,
        )


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class ReportingTests(ReportsMixin, TestCase):
    url = reverse("reliability-report")

    def post(self, route, stop, kind=Kind.NEVER_CAME):
        return self.client.post(self.url, {"route": route, "stop": stop, "kind": kind}, follow=True)

    def test_a_logged_in_rider_reports_a_bus_that_never_came(self, urlopen, now):
        urlopen.side_effect = feed()
        self.client.force_login(self.rider)

        response = self.post(1, 12)

        self.assertRedirects(response, reverse("reliability"))
        self.assertContains(
            response,
            "Thanks! We logged your report for Blue - Weekday Daytime at Phelps Gate: never came.",
        )
        saved = RiderReport.objects.get()
        self.assertEqual(
            (
                saved.user,
                saved.route_id,
                saved.route_name,
                saved.stop_id,
                saved.stop_name,
                saved.kind,
                saved.created_at,
                saved.is_sample,
            ),
            (self.rider, 1, "Blue - Weekday Daytime", 12, "Phelps Gate", Kind.NEVER_CAME, NOW, False),
        )

    def test_a_rider_can_report_a_skipped_stop_too(self, urlopen, now):
        urlopen.side_effect = feed()
        self.client.force_login(self.rider)

        self.post(1, 10, kind=Kind.SKIPPED)

        self.assertEqual(RiderReport.objects.get().kind, Kind.SKIPPED)

    def test_the_form_offers_running_routes_and_their_stops(self, urlopen, now):
        urlopen.side_effect = feed()
        self.client.force_login(self.rider)

        response = self.client.get(reverse("reliability"))

        routes = [label for value, label in response.context["form"].fields["route"].choices if value]
        self.assertEqual(routes, ["Blue - Weekday Daytime", "Gold Route"])  # Blue West isn't running
        stop_groups = {
            label: stops for label, stops in response.context["form"].fields["stop"].choices if label
        }
        self.assertEqual(list(stop_groups), ["Blue - Weekday Daytime", "Gold Route"])
        self.assertEqual(stop_groups["Gold Route"], [(10, "Science Hill")])
        self.assertContains(response, "Never came")
        self.assertContains(response, "Skipped my stop")
        # Never came is checked by default.
        self.assertContains(response, 'value="never_came" checked')

    def test_one_report_per_rider_per_route_and_stop_every_15_minutes(self, urlopen, now):
        urlopen.side_effect = feed()
        self.client.force_login(self.rider)
        self.post(1, 12)

        now.return_value = NOW + dt.timedelta(minutes=14)
        response = self.post(1, 12)
        self.assertContains(response, "You reported Blue - Weekday Daytime at Phelps Gate in the last 15 minutes.")
        self.assertContains(response, "You can report it again at 2:55 PM.")

        self.assertEqual(self.post(1, 10).status_code, 200)  # another stop is fine
        self.assertEqual(self.post(15, 10).status_code, 200)  # so is another route
        self.client.force_login(self.other)
        self.post(1, 12)  # so is another rider
        self.assertEqual(RiderReport.objects.count(), 4)

        self.client.force_login(self.rider)
        now.return_value = NOW + dt.timedelta(minutes=15)
        self.assertContains(self.post(1, 12), "Thanks!")
        self.assertEqual(RiderReport.objects.filter(user=self.rider, route_id=1, stop_id=12).count(), 2)

    def test_sample_reports_dont_block_a_real_one(self, urlopen, now):
        urlopen.side_effect = feed()
        self.add(1, 12, Kind.NEVER_CAME, at(minutes=2), user=self.rider, is_sample=True)
        self.client.force_login(self.rider)
        self.assertContains(self.post(1, 12), "Thanks!")

    def test_the_stop_must_be_on_the_route_and_choices_must_be_real(self, urlopen, now):
        urlopen.side_effect = feed()
        self.client.force_login(self.rider)

        self.assertContains(self.post(15, 12), "on the route you picked")
        self.assertContains(self.post(16, 10), "Select a valid choice")  # not running
        self.assertContains(self.post(1, ""), "This field is required")
        self.assertContains(self.post(1, 10, kind="wrong"), "Select a valid choice")
        self.assertFalse(RiderReport.objects.exists())

    def test_riders_must_log_in_to_report(self, urlopen, now):
        urlopen.side_effect = feed()

        response = self.client.post(self.url, {"route": 1, "stop": 10, "kind": Kind.NEVER_CAME})

        self.assertRedirects(
            response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False
        )
        self.assertFalse(RiderReport.objects.exists())
        self.assertContains(self.client.get(reverse("reliability")), "Log in to report")

    def test_reporting_is_paused_when_the_feed_is_down(self, urlopen, now):
        urlopen.side_effect = URLError("timed out")
        self.client.force_login(self.rider)

        response = self.post(1, 10)

        self.assertContains(response, "reporting is paused")
        self.assertFalse(RiderReport.objects.exists())

    def test_when_nothing_runs_there_is_nothing_to_report(self, urlopen, now):
        urlopen.side_effect = feed(routes=[dict(route, active=False) for route in ROUTES])
        self.client.force_login(self.rider)

        response = self.client.get(reverse("reliability"))

        self.assertContains(response, "No shuttles are running now")
        self.assertIsNone(response.context["form"])


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class ReliabilityPageTests(ReportsMixin, TestCase):
    def test_the_page_lists_the_last_day_of_reports(self, urlopen, now):
        urlopen.side_effect = feed()
        self.add(1, 12, Kind.NEVER_CAME, at(hours=2))
        self.add(15, 10, Kind.SKIPPED, at(hours=1))
        self.add(1, 10, Kind.NEVER_CAME, at(hours=25))  # more than a day old

        response = self.client.get(reverse("reliability"))

        self.assertEqual(
            [(report.route_id, report.kind) for report in response.context["recent"]],
            [(15, Kind.SKIPPED), (1, Kind.NEVER_CAME)],  # newest first
        )
        self.assertContains(response, "Gold Route")
        self.assertContains(response, "Skipped my stop")
        self.assertNotContains(response, "No reports yet today")

    def test_an_empty_list_says_so(self, urlopen, now):
        urlopen.side_effect = feed()
        self.assertContains(self.client.get(reverse("reliability")), "No reports yet today")

    def test_the_navbar_links_to_reliability(self, urlopen, now):
        self.assertContains(self.client.get("/"), f'href="{reverse("reliability")}"')
        urlopen.assert_not_called()
