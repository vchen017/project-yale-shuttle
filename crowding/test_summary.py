"""The admin's crowding summary by route and hour (#18). Staff only; removed
reports (#16) don't count. The Downtowner feed is never called by this page,
but is mocked anyway, and `timezone.now` is frozen, so no test reaches the
network."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import CrowdReport
from .services import crowding_summary
from .tests import NOW, Level, ReportsMixin, at

URL = reverse("crowding-summary")


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class SummaryTests(ReportsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_user("admin@example.com", password="an-admin-password-1", is_staff=True)

    def rows(self, response):
        return {row.route_id: row for row in response.context["rows"]}

    def test_averages_each_route_and_hour_for_the_chosen_range(self, urlopen, now):
        self.add(Level.EMPTY, at(days_ago=1, hour=14))
        self.add(Level.STANDING, at(days_ago=2, hour=14))
        self.add(Level.SEATS, at(days_ago=1, hour=8), route_id=15)
        self.add(Level.FULL, at(days_ago=20, hour=14))  # outside the range
        self.client.force_login(self.staff)

        response = self.client.get(URL, {"from": "2026-11-14", "to": "2026-11-18"})

        rows = self.rows(response)
        self.assertEqual(set(rows), {1, 15})
        blue = rows[1].cells[14]
        self.assertEqual((blue.average, blue.count, blue.full_count), (1.0, 2, 0))
        self.assertEqual(rows[15].cells[8].average, 1.0)
        self.assertEqual([h.hour for h in response.context["hours"]], [8, 14])
        self.assertContains(response, "Blue - Weekday Daytime")
        self.assertContains(response, "2 PM")

    def test_the_range_includes_both_end_days_in_eastern_time(self, urlopen, now):
        self.add(Level.SEATS, at(days_ago=3, hour=0))  # 12:20 AM Eastern on the 15th, still the 15th
        self.add(Level.SEATS, at(days_ago=1, hour=23))  # 11:20 PM Eastern on the 17th
        self.client.force_login(self.staff)
        response = self.client.get(URL, {"from": "2026-11-15", "to": "2026-11-17"})
        self.assertEqual(self.rows(response)[1].count, 2)
        response = self.client.get(URL, {"from": "2026-11-16", "to": "2026-11-16"})
        self.assertEqual(response.context["rows"], [])

    def test_hours_with_a_full_report_are_highlighted(self, urlopen, now):
        self.add(Level.FULL, at(days_ago=1, hour=14))
        self.add(Level.EMPTY, at(days_ago=2, hour=14))
        self.add(Level.EMPTY, at(days_ago=1, hour=9))
        self.client.force_login(self.staff)

        response = self.client.get(URL)

        row = self.rows(response)[1]
        self.assertEqual((row.cells[14].average, row.cells[14].full_count), (1.5, 1))
        self.assertEqual(row.full_count, 1)
        self.assertContains(response, "crowd-full-cell", count=1)  # only the 2 PM cell
        self.assertContains(response, "1 reported full")

    def test_removed_reports_dont_count(self, urlopen, now):
        bad = self.add(Level.FULL, at(days_ago=1, hour=14))
        self.add(Level.EMPTY, at(days_ago=2, hour=14))
        CrowdReport.objects.filter(pk=bad.pk).update(removed_at=NOW)
        self.client.force_login(self.staff)

        response = self.client.get(URL)

        cell = self.rows(response)[1].cells[14]
        self.assertEqual((cell.average, cell.count, cell.full_count), (0.0, 1, 0))
        self.assertNotContains(response, "crowd-full-cell")

    def test_routes_are_told_apart_by_id_and_named_by_their_latest_report(self, urlopen, now):
        self.add(Level.SEATS, at(days_ago=5, hour=10), route_id=16)  # Blue West
        self.add(Level.SEATS, at(days_ago=4, hour=10))  # Blue - Weekday Daytime
        CrowdReport.objects.create(
            user=self.other, route_id=1, route_name="Blue", level=Level.SEATS, created_at=at(days_ago=3, hour=10)
        )
        rows, hours, samples = crowding_summary(NOW.date().replace(day=1), NOW.date())
        self.assertEqual([(r.route_id, r.name, r.count) for r in rows], [(1, "Blue", 2), (16, "Blue West", 1)])
        self.assertFalse(samples)

    def test_says_when_demo_reports_are_included(self, urlopen, now):
        self.add(Level.SEATS, at(days_ago=1, hour=10), is_sample=True)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(URL), "Includes sample reports")

    def test_an_empty_range_says_so(self, urlopen, now):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(URL), "No reports from")

    def test_a_bad_range_falls_back_to_the_last_28_days_with_a_message(self, urlopen, now):
        self.add(Level.SEATS, at(days_ago=27, hour=10))
        self.add(Level.SEATS, at(days_ago=40, hour=10))
        self.client.force_login(self.staff)
        for params, message in [
            ({"from": "soon"}, "Use YYYY-MM-DD dates"),
            ({"to": "nope"}, "Use YYYY-MM-DD dates"),
            ({"from": "2026-11-18", "to": "2026-11-01"}, "is after"),
            ({"from": "2026-01-01", "to": "2026-11-18"}, "at most 92 days"),
        ]:
            response = self.client.get(URL, params)
            self.assertContains(response, message)
            self.assertEqual(self.rows(response)[1].count, 1)

    def test_riders_and_visitors_cant_open_it(self, urlopen, now):
        self.add(Level.FULL, at(days_ago=1, hour=14))
        for who in (None, self.rider):
            if who:
                self.client.force_login(who)
            response = self.client.get(URL)
            self.assertRedirects(response, f"{reverse('admin:login')}?next={URL}", fetch_redirect_response=False)

    def test_the_crowding_page_links_staff_to_it(self, urlopen, now):
        from .tests import ROUTES, feed

        urlopen.side_effect = feed(ROUTES)
        self.client.force_login(self.rider)
        self.assertNotContains(self.client.get(reverse("crowding")), URL)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("crowding")), f'href="{URL}"')
