"""Crowding reports (#12). The Downtowner feed is mocked at
transit.downtowner.urlopen, answering by endpoint, so no test reaches the
network; `timezone.now` is frozen where times matter."""

import datetime as dt
import json
from io import StringIO
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from . import demo
from .models import CrowdReport
from .services import average_level, crowding

ET = ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 11, 18, 14, 40, tzinfo=ET)  # a Wednesday, 2:40 PM Eastern
Level = CrowdReport.Level

ROUTES = [
    {"id": 1, "name": "Blue - Weekday Daytime", "short_name": "BD", "color": "4472C4", "active": True, "stops": [10]},
    {"id": 15, "name": "Gold Route", "short_name": "G", "color": "FCE36C", "active": True, "stops": [10]},
    {"id": 16, "name": "Blue West", "short_name": "BW", "color": "3498DB", "active": False, "stops": [10]},
]
STOPS = [{"id": 10, "name": "Science Hill", "lat": 41.316, "lon": -72.922, "description": ""}]


def feed(routes=ROUTES):
    """A urlopen stand-in that answers the routes and stops feeds."""
    answers = {"/routes_routes.php": routes, "/routes_stops.php": STOPS}

    def urlopen(request, timeout):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(answers[urlsplit(request.full_url).path]).encode()
        return response

    return urlopen


def at(minutes_ago=0, days_ago=0, hour=None):
    moment = NOW - dt.timedelta(minutes=minutes_ago, days=days_ago)
    return moment.replace(hour=hour, minute=20) if hour is not None else moment


class ReportsMixin:
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.rider = User.objects.create_user("rider@example.com", password="a-rider-password-1")
        self.other = User.objects.create_user("other@example.com")

    def add(self, level, when, route_id=1, user=None, is_sample=False):
        return CrowdReport.objects.create(
            user=user or self.other,
            route_id=route_id,
            route_name={1: "Blue - Weekday Daytime", 15: "Gold Route", 16: "Blue West"}[route_id],
            level=level,
            created_at=when,
            is_sample=is_sample,
        )


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class ReportingTests(ReportsMixin, TestCase):
    url = reverse("crowding-report")

    def post(self, route, level):
        return self.client.post(self.url, {"route": route, "level": level}, follow=True)

    def test_a_logged_in_rider_reports_their_route_and_level(self, urlopen, now):
        urlopen.side_effect = feed()
        self.client.force_login(self.rider)

        response = self.post(1, Level.STANDING)

        self.assertRedirects(response, reverse("crowding"))
        self.assertContains(response, "Thanks! Riders now see your report for Blue - Weekday Daytime.")
        saved = CrowdReport.objects.get()
        self.assertEqual(
            (saved.user, saved.route_id, saved.route_name, saved.level, saved.created_at, saved.is_sample),
            (self.rider, 1, "Blue - Weekday Daytime", Level.STANDING, NOW, False),
        )

    def test_the_form_offers_the_routes_running_now_and_all_four_levels(self, urlopen, now):
        urlopen.side_effect = feed()
        self.client.force_login(self.rider)

        response = self.client.get(reverse("crowding"))

        routes = [label for value, label in response.context["form"].fields["route"].choices if value]
        self.assertEqual(routes, ["Blue - Weekday Daytime", "Gold Route"])  # Blue West isn't running
        for label in ["Empty", "Seats available", "Standing room", "Full (can&#x27;t board)"]:
            self.assertContains(response, label)

    def test_one_report_per_rider_per_route_every_10_minutes(self, urlopen, now):
        urlopen.side_effect = feed()
        self.client.force_login(self.rider)
        self.post(1, Level.SEATS)

        now.return_value = NOW + dt.timedelta(minutes=9)
        response = self.post(1, Level.FULL)
        self.assertContains(response, "You reported Blue - Weekday Daytime in the last 10 minutes.")
        self.assertContains(response, "You can report it again at 2:50 PM.")

        self.assertEqual(self.post(15, Level.FULL).status_code, 200)  # another route is fine
        self.client.force_login(self.other)
        self.post(1, Level.FULL)  # so is another rider
        self.assertEqual(CrowdReport.objects.count(), 3)

        self.client.force_login(self.rider)
        now.return_value = NOW + dt.timedelta(minutes=10)
        self.assertContains(self.post(1, Level.FULL), "Thanks!")
        self.assertEqual(CrowdReport.objects.filter(user=self.rider, route_id=1).count(), 2)

    def test_sample_reports_dont_block_a_real_one(self, urlopen, now):
        urlopen.side_effect = feed()
        self.add(Level.FULL, at(minutes_ago=2), user=self.rider, is_sample=True)
        self.client.force_login(self.rider)
        self.assertContains(self.post(1, Level.SEATS), "Thanks!")

    def test_routes_not_running_and_unknown_levels_are_refused(self, urlopen, now):
        urlopen.side_effect = feed()
        self.client.force_login(self.rider)

        self.assertContains(self.post(16, Level.SEATS), "Select a valid choice")
        self.assertContains(self.post(1, 7), "Select a valid choice")
        self.assertContains(self.post(1, ""), "This field is required")
        self.assertFalse(CrowdReport.objects.exists())

    def test_riders_must_log_in_to_report(self, urlopen, now):
        urlopen.side_effect = feed()

        response = self.client.post(self.url, {"route": 1, "level": Level.SEATS})

        self.assertRedirects(response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False)
        self.assertFalse(CrowdReport.objects.exists())
        self.assertContains(self.client.get(reverse("crowding")), "Log in to report")

    def test_reporting_is_paused_when_the_feed_is_down(self, urlopen, now):
        urlopen.side_effect = URLError("timed out")
        self.client.force_login(self.rider)

        response = self.client.post(self.url, {"route": 1, "level": Level.SEATS})

        self.assertContains(response, "reporting is paused")
        self.assertFalse(CrowdReport.objects.exists())


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class CrowdingPageTests(ReportsMixin, TestCase):
    def page(self):
        return self.client.get(reverse("crowding"))

    def route(self, response, route_id):
        return next(route for route in response.context["routes"] if route.route_id == route_id)

    def test_current_crowding_comes_from_the_last_30_minutes(self, urlopen, now):
        urlopen.side_effect = feed()
        self.add(Level.FULL, at(minutes_ago=5))
        self.add(Level.STANDING, at(minutes_ago=25))
        self.add(Level.EMPTY, at(minutes_ago=31))  # too old to count
        self.add(Level.EMPTY, at(minutes_ago=1), is_sample=True)  # samples never count as now

        response = self.page()

        blue = self.route(response, 1)
        self.assertEqual((blue.current_level, blue.current_count), (Level.FULL, 2))  # 2.5 rounds up
        self.assertContains(response, "2 reports in the last 30 minutes, latest 2:35 PM")
        self.assertIsNone(self.route(response, 15).current_level)
        self.assertContains(response, "No reports in the last 30 minutes")

    def test_typical_crowding_by_eastern_hour_from_past_reports(self, urlopen, now):
        urlopen.side_effect = feed()
        for days_ago, level in [(1, Level.SEATS), (2, Level.STANDING), (7, Level.SEATS)]:
            self.add(level, at(days_ago=days_ago, hour=14))  # 2:20 PM Eastern
        for days_ago in [1, 2, 3]:
            self.add(Level.FULL, at(days_ago=days_ago, hour=8))
        self.add(Level.EMPTY, at(days_ago=1, hour=11))  # one report isn't a pattern
        self.add(Level.EMPTY, at(days_ago=29, hour=14))  # older than four weeks

        response = self.page()

        blue = self.route(response, 1)
        self.assertEqual([(h.hour, h.level, h.count) for h in blue.by_hour], [(8, 3, 3), (14, 1, 3)])
        self.assertEqual(blue.typical_now.level, Level.SEATS)  # now is 2:40 PM
        self.assertContains(response, "Usually around 2 PM")
        self.assertContains(response, "from 3 past reports")
        self.assertContains(response, "<th scope=\"row\" class=\"fw-normal\">8 AM</th>")
        self.assertNotContains(response, "Includes sample reports")
        self.assertContains(response, "Not enough past reports for this hour")  # Gold

    def test_typical_crowding_says_when_it_includes_samples(self, urlopen, now):
        urlopen.side_effect = feed()
        for days_ago in [1, 2, 3]:
            self.add(Level.SEATS, at(days_ago=days_ago, hour=14), is_sample=True)
        self.assertContains(self.page(), "Includes sample reports from our demo data.")

    def test_route_chips_use_the_route_colour_with_readable_text(self, urlopen, now):
        urlopen.side_effect = feed()
        response = self.page()
        self.assertContains(response, "background-color: #4472C4; color: #fff;")
        self.assertContains(response, "background-color: #FCE36C; color: #212529;")

    def test_with_the_feed_down_reported_routes_still_show(self, urlopen, now):
        urlopen.side_effect = URLError("timed out")
        self.add(Level.STANDING, at(minutes_ago=5), route_id=16)

        response = self.page()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "We can't load the routes running now")
        self.assertEqual([route.name for route in response.context["routes"]], ["Blue West"])
        self.assertEqual(self.route(response, 16).current_level, Level.STANDING)

    def test_late_at_night_it_says_nothing_is_running(self, urlopen, now):
        urlopen.side_effect = feed(routes=[dict(route, active=False) for route in ROUTES])
        self.add(Level.SEATS, at(days_ago=1, hour=14), route_id=15)

        response = self.page()

        self.assertContains(response, "No shuttles are running now")
        self.assertIsNone(response.context["form"])
        self.assertEqual([route.route_id for route in response.context["routes"]], [15])
        # The feed's colour, not the default, since the feed still lists the route.
        self.assertContains(response, "background-color: #FCE36C")

    def test_the_navbar_links_to_crowding(self, urlopen, now):
        self.assertContains(self.client.get("/"), f'href="{reverse("crowding")}"')
        urlopen.assert_not_called()


class AverageLevelTests(TestCase):
    def test_rounds_to_the_nearest_level_with_halves_going_busier(self):
        self.assertEqual(average_level([0, 1]), 1)
        self.assertEqual(average_level([1, 1, 2]), 1)
        self.assertEqual(average_level([3]), 3)

    def test_no_routes_means_no_crowding(self):
        self.assertEqual(crowding([]), [])


@mock.patch("django.utils.timezone.now", return_value=NOW)
class DemoTests(ReportsMixin, TestCase):
    def seed(self):
        demo.seed({"rider": self.rider}, StringIO())

    def test_creates_weekday_samples_once_and_none_as_current(self, now):
        self.seed()
        self.seed()

        reports = CrowdReport.objects.all()
        # 10 weekdays in the two weeks before Wednesday Nov 18, 5 routes, 11 hours.
        self.assertEqual(reports.count(), 10 * 5 * 11)
        self.assertTrue(all(report.is_sample and report.user == self.rider for report in reports))
        self.assertTrue(all(report.created_at.astimezone(ET).weekday() < 5 for report in reports))
        blue = crowding([{"id": 1, "name": "Blue", "label": "Blue", "color": "4472C4"}])[0]
        self.assertIsNone(blue.current_level)
        self.assertEqual(blue.typical_now.level, demo.BY_HOUR[14])
        self.assertEqual({h.hour: h.level for h in blue.by_hour}, demo.BY_HOUR)
        self.assertTrue(blue.includes_samples)

    def test_replaces_samples_once_they_are_a_week_old_and_keeps_real_reports(self, now):
        self.seed()
        real = self.add(Level.FULL, at(minutes_ago=5))
        now.return_value = NOW + dt.timedelta(days=8)

        self.seed()

        newest = CrowdReport.objects.filter(is_sample=True).order_by("-created_at").first()
        self.assertEqual(newest.created_at.astimezone(ET).date(), dt.date(2026, 11, 25))
        self.assertTrue(CrowdReport.objects.filter(pk=real.pk).exists())
