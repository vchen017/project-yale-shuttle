"""The accuracy dashboard (#17). Synthetic history builds a day where the
feed's countdown revises a bus closer than it first promised, so every
scored query has an error computable by hand; nothing here reaches the
network, and the dashboard's date range is passed explicitly."""

import datetime as dt
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from history import accuracy, predictions
from history.models import Eta, Poll, RiderReport

ET = ZoneInfo("America/New_York")
UTC = dt.UTC
DAYS = [dt.date(2026, 11, day) for day in range(2, 8)]  # Mon Nov 2 - Sat Nov 7, all EST
START, END = DAYS[0], DAYS[-1]


def eastern(day, hour=0, minute=0):
    """A moment on the Eastern clock, as it is stored: UTC."""
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=ET).astimezone(UTC)


def add_approach(day, stop_id, bus_id, stop_name):
    """One bus's morning: first promised at 10:30, revised at the last minute
    to 10:26, then a second approach arriving 11:00. The implied arrivals
    are the feed's word; the arrival events are what actually happened."""
    schedule = [
        (10, 5, 25),  # implied 10:30, and so on down to...
        (10, 10, 20),
        (10, 15, 15),
        (10, 20, 10),
        (10, 25, 1),  # ...revised: the bus now due 10:26, and it arrives then
        (10, 35, 25),  # second approach, implied 11:00
        (10, 40, 20),
        (10, 45, 15),
        (10, 50, 10),
        (10, 55, 5),  # arrives 11:00
        (11, 5, 25),  # a third promise the history never sees kept
    ]
    for hour, minute, eta in schedule:
        moment = eastern(day, hour, minute)
        poll, _ = Poll.objects.get_or_create(
            polled_at=moment, defaults={"status": Poll.Status.RECORDED}
        )
        Eta.objects.create(
            poll=poll,
            polled_at=moment,
            stop_id=stop_id,
            stop_name=stop_name,
            route_id=1,
            route_name="Test route",
            bus_id=bus_id,
            bus_name=f"#{bus_id}",
            eta_minutes=eta,
            calculation_time=moment,
        )


def seed_history():
    for day in DAYS:
        add_approach(day, 100, 7, "Science Park")
        add_approach(day, 200, 8, "Phelps Gate")


def add_report(route_id, stop_id, when, user, is_sample=False):
    return RiderReport.objects.create(
        user=user,
        route_id=route_id,
        route_name="Test route",
        stop_id=stop_id,
        stop_name="A stop",
        kind=RiderReport.Kind.NEVER_CAME,
        created_at=when,
        is_sample=is_sample,
    )


class AccuracyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cache.clear()
        User = get_user_model()
        cls.rider = User.objects.create_user("rider@example.com", password="a-rider-password-1")
        seed_history()
        add_report(1, 100, eastern(START, 10, 20), cls.rider)  # real, at hour 10
        add_report(1, 200, eastern(START + dt.timedelta(days=2), 8, 5), cls.rider)  # real, hour 8
        add_report(1, 100, eastern(START + dt.timedelta(days=1), 10, 40), cls.rider, is_sample=True)
        add_report(99, 1, eastern(START + dt.timedelta(days=1), 12), cls.rider)  # a route history never served
        add_report(1, 100, eastern(END + dt.timedelta(days=1), 10), cls.rider)  # outside the window
        # Scored days: what the holdout discipline picks, deterministically.
        cls.scored = sorted(predictions.held_out_days(set(DAYS), accuracy.HOLDOUT_MODULUS))
        cls.result = accuracy.accuracy(eastern(START), eastern(END + dt.timedelta(days=1)))

    def test_scored_days_follow_the_holdout_discipline(self):
        self.assertTrue(self.scored)  # the fixture only works if some day is scored
        self.assertEqual(self.result["scored_days"], self.scored)
        self.assertEqual(self.result["history_days"], len(DAYS))

    def test_every_side_errors_as_the_revision_implied(self):
        """Five queries per stop per scored day: +4, +4, -28, 0, 0.

        The feed promises 10:30 and delivers 10:26, so both we and the
        official ETA say the bus is 4 minutes farther than it is; when the
        revised bus comes due, both floor at 0 while the next bus is 28
        away."""
        overall = self.result["overall"]
        self.assertEqual(overall["queries"], 10 * len(self.scored))
        for side in ["ours", "official"]:
            self.assertEqual(round(overall[side]["mean"], 1), -4.0)
            self.assertEqual(round(overall[side]["mae"], 1), 7.2)
            self.assertEqual(overall[side]["answered"], overall["queries"])
            self.assertEqual(overall[side]["mean_display"], "-4.0")
        # The history layer answered everywhere too, and it stayed sane:
        # trained on days identical to the scored ones, it can't be far off.
        self.assertEqual(overall["history"]["answered"], overall["queries"])
        self.assertLessEqual(round(overall["history"]["mae"], 1), 30.0)
        self.assertEqual(self.result["no_prediction"], 0)
        self.assertEqual(self.result["not_served"], 0)

    def test_by_route_groups_the_errors_and_the_reports(self):
        routes = {data["key"]: data for data in self.result["by_route"]}
        self.assertEqual(set(routes), {1, 99})
        test_route = routes[1]
        self.assertEqual(test_route["queries"], 10 * len(self.scored))
        self.assertEqual(test_route["reports"], 2)
        self.assertEqual(test_route["sample_reports"], 1)
        self.assertEqual(round(test_route["ours"]["mean"], 1), -4.0)
        # A report-only route still shows, by name, with no errors to show.
        self.assertEqual(routes[99]["queries"], 0)
        self.assertEqual(routes[99]["reports"], 1)
        self.assertIsNone(routes[99]["ours"])
        self.assertEqual(routes[99]["label"], "Route 99")

    def test_by_stop_names_each_stop_and_counts_its_reports(self):
        stops = {data["key"]: data for data in self.result["by_stop"]}
        # The report-only stop (route 99, stop 1) shows too, with nothing scored.
        self.assertEqual(set(stops), {(1, 100), (1, 200), (99, 1)})
        science_park = stops[(1, 100)]
        self.assertEqual(science_park["label"], "Test route · Science Park")
        self.assertEqual(science_park["reports"], 1)
        self.assertEqual(science_park["sample_reports"], 1)
        phelps = stops[(1, 200)]
        self.assertEqual(phelps["reports"], 1)
        self.assertEqual(phelps["sample_reports"], 0)
        self.assertEqual(round(phelps["official"]["mae"], 1), 7.2)
        stray = stops[(99, 1)]
        self.assertEqual(stray["queries"], 0)
        self.assertEqual(stray["label"], "Route 99 · stop 1")

    def test_by_hour_puts_reports_next_to_the_hours_around_them(self):
        hours = {data["key"]: data for data in self.result["by_hour"]}
        self.assertEqual(hours[10]["queries"], 10 * len(self.scored))  # every query is at 10 AM
        self.assertEqual(hours[10]["reports"], 1)
        self.assertEqual(hours[10]["sample_reports"], 1)
        self.assertEqual(hours[10]["label"], "10 AM")
        # A hour with a report but no scored queries shows the report alone.
        self.assertEqual(hours[8]["queries"], 0)
        self.assertEqual(hours[8]["reports"], 1)
        self.assertIsNone(hours[8]["ours"])
        self.assertEqual(hours[8]["label"], "8 AM")

    def test_reports_outside_the_window_are_not_counted(self):
        self.assertEqual(self.result["reports"], 3)
        self.assertEqual(self.result["sample_reports"], 1)


class DashboardPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cache.clear()
        User = get_user_model()
        cls.staff = User.objects.create_user("admin@example.com", password="an-admin-password-1", is_staff=True)
        cls.rider = User.objects.create_user("rider@example.com", password="a-rider-password-1")
        seed_history()
        add_report(1, 100, eastern(DAYS[0], 10, 20), cls.rider)

    def test_staff_sees_the_numbers_for_the_range_they_pick(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accuracy-dashboard"), {"from": "2026-11-02", "to": "2026-11-07"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Prediction accuracy")
        self.assertContains(response, "Test route")
        self.assertContains(response, "-4.0 / 7.2")
        self.assertContains(response, "10 AM")
        self.assertContains(response, "Science Park")

    def test_results_are_cached_for_the_range(self):
        self.client.force_login(self.staff)
        self.client.get(reverse("accuracy-dashboard"), {"from": "2026-11-02", "to": "2026-11-07"})
        self.assertIsNotNone(cache.get("history-accuracy:2026-11-02:2026-11-07"))

    def test_riders_and_anonymous_visitors_are_sent_away(self):
        self.client.force_login(self.rider)
        response = self.client.get(reverse("accuracy-dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
        self.client.logout()
        response = self.client.get(reverse("accuracy-dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_a_backwards_range_falls_back_with_a_message(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("accuracy-dashboard"), {"from": "2026-11-07", "to": "2026-11-02"})
        self.assertContains(response, "is after")
        self.assertContains(response, "Showing the default range")

    def test_an_oversized_range_is_refused(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("accuracy-dashboard"), {"from": "2026-10-01", "to": "2026-11-07"})
        self.assertContains(response, "at most 31 days")

    def test_a_bad_date_is_refused(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("accuracy-dashboard"), {"from": "November"})
        self.assertContains(response, "Use YYYY-MM-DD dates")

    def test_the_navbar_links_the_dashboard_for_staff_only(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get("/"), 'href="/reliability/dashboard/"')
        self.client.force_login(self.rider)
        self.assertNotContains(self.client.get("/"), "reliability/dashboard/")
