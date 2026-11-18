"""Detour detection (#55). The Downtowner feed is mocked at
transit.downtowner.urlopen, answering with fixture routes, so no test reaches
the network; the bus positions come from fixture polls (#22)."""

import datetime as dt
import io
import json
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlsplit

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert
from detours import demo, detect
from history.models import BusPosition, Poll
from transit import downtowner

# A straight north-south path, the way the feed's routes answer carries one:
# a flat [lat, lon, lat, lon, ...] list. 41.305 to 41.315 is about 1.1 km.
PATH = [41.305, -72.94, 41.315, -72.94]
# About 500 m west of that path, clearly farther than OFF_PATH_METERS.
OFF_LON = -72.934

ROUTES = [
    {
        "id": 1,
        "name": "Blue - Weekday Daytime",
        "short_name": "BD",
        "color": "4472C4",
        "active": True,
        "stops": [10],
        "path": PATH,
    },
    {
        # No official path to leave: never flagged, however far it strays.
        "id": 2,
        "name": "Orange",
        "short_name": "O",
        "color": "F5A623",
        "active": True,
        "stops": [10],
        "path": [],
    },
    {
        # Not running: never flagged either.
        "id": 3,
        "name": "Red - Weekend",
        "short_name": "R",
        "color": "C00000",
        "active": False,
        "stops": [10],
        "path": PATH,
    },
]

STOPS = [{"id": 10, "name": "Science Hill", "lat": 41.31, "lon": -72.92, "description": ""}]


def feed_answer(data):
    """What urlopen returns for one of the feed's JSON answers."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(data).encode()
    return response


def feed_by_path(answers):
    """A urlopen stand-in that answers each feed endpoint."""
    def urlopen(request, timeout):
        return feed_answer(answers[urlsplit(request.full_url).path])

    return urlopen


def poll(minutes_ago):
    """A recorded poll from that many minutes ago (#22)."""
    return Poll.objects.create(polled_at=timezone.now() - dt.timedelta(minutes=minutes_ago))


def bus(at_poll, route_id, lon):
    """One bus's position in that poll."""
    return BusPosition.objects.create(
        poll=at_poll,
        polled_at=at_poll.polled_at,
        bus_id=812,
        bus_name="812",
        route_id=route_id,
        route_name=f"Route {route_id}",
        lat=41.31,
        lon=lon,
    )


def three_off_path_polls(route_id=1):
    """Three polls in a row, each with the bus off the route's path."""
    return [bus(poll(minutes), route_id, OFF_LON) for minutes in (10, 5, 0)]


@mock.patch("transit.downtowner.urlopen")
class DetourDetectionTests(TestCase):
    """A route is flagged after three polls in a row with every bus off its
    official path (#55). Deliberately hard to flag: one bus straying, one bus
    back on the path, one poll with none running — each breaks the streak."""

    def setUp(self):
        cache.clear()  # the detection is cached for a minute; each test starts cold
        self.addCleanup(cache.clear)

    def test_three_off_path_polls_in_a_row_flag_the_route(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)
        buses = three_off_path_polls()

        flagged = detect.detoured_routes()

        self.assertEqual(
            flagged,
            [
                {
                    "route_id": 1,
                    "route_name": "Blue - Weekday Daytime",
                    "bus_count": 1,
                    "since": buses[-1].polled_at,
                }
            ],
        )

    def test_a_bus_back_on_its_path_breaks_the_streak(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)
        bus(poll(10), 1, OFF_LON)
        bus(poll(5), 1, -72.94)  # back where the route belongs
        bus(poll(0), 1, OFF_LON)

        self.assertEqual(detect.detoured_routes(), [])

    def test_a_route_without_an_official_path_is_never_flagged(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)
        three_off_path_polls(route_id=2)

        self.assertEqual(detect.detoured_routes(), [])

    def test_an_inactive_route_is_never_flagged(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)
        three_off_path_polls(route_id=3)

        self.assertEqual(detect.detoured_routes(), [])

    def test_fewer_than_three_recent_polls_cannot_say_in_a_row(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)
        bus(poll(3 * 60), 1, OFF_LON)  # three hours ago: older than FRESH_HOURS
        bus(poll(10), 1, OFF_LON)
        bus(poll(5), 1, OFF_LON)

        self.assertEqual(detect.detoured_routes(), [])

    def test_a_missing_poll_breaks_the_streak(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)
        bus(poll(10), 1, OFF_LON)
        middle = poll(5)
        bus(middle, 1, OFF_LON)
        middle.status = Poll.Status.MISSING
        middle.save()
        bus(poll(0), 1, OFF_LON)

        self.assertEqual(detect.detoured_routes(), [])

    def test_no_buses_in_the_newest_poll_says_nothing(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)
        bus(poll(10), 1, OFF_LON)
        bus(poll(5), 1, OFF_LON)
        bus(poll(0), 2, OFF_LON)  # the only bus out now is on another route

        self.assertEqual(detect.detoured_routes(), [])

    def test_the_answer_is_cached_for_a_minute(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)
        three_off_path_polls()
        first = detect.detoured_routes()

        urlopen.side_effect = URLError("feed down")  # a second call reads the cache
        self.assertEqual(detect.detoured_routes(), first)

    def test_the_feed_being_down_raises(self, urlopen):
        urlopen.side_effect = URLError("timed out")
        three_off_path_polls()

        with self.assertRaises(downtowner.FeedUnavailable):
            detect.detoured_routes()


class MetersOffPathTests(TestCase):
    """The geometry: the distance from a point to the nearest point of the
    path, measured to the segment, not just the nearest corner."""

    def test_a_point_on_the_path_measures_zero(self):
        self.assertLess(detect.meters_off_path((41.31, -72.94), PATH), 1.0)

    def test_a_point_off_the_path_measures_the_real_distance(self):
        # 0.006 degrees of longitude at New Haven's latitude is about 500 m.
        self.assertAlmostEqual(detect.meters_off_path((41.31, OFF_LON), PATH), 502, delta=30)

    def test_a_path_with_one_corner_measures_to_the_corner(self):
        self.assertAlmostEqual(
            detect.meters_off_path((41.31, -72.935), [41.31, -72.94]), 418, delta=30
        )

    def test_a_path_too_thin_to_be_a_line_measures_zero(self):
        self.assertEqual(detect.meters_off_path((41.31, -72.94), []), 0.0)


@mock.patch("transit.next_buses.predict_minutes", return_value=None)
@mock.patch("transit.downtowner.urlopen")
class NextBusesBadgeTests(TestCase):
    """The next-buses page (#24) marks a flagged route with "possible
    detour" (#55), next to the times riders are already reading."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def feed_down(self, urlopen):
        urlopen.side_effect = feed_by_path(
            {
                "/routes_routes.php": ROUTES,
                "/routes_stops.php": STOPS,
                "/routes_eta.php": {"etas": {}},
                "/routes_buses.php": [],
            }
        )

    def test_a_detoured_route_is_marked_and_a_healthy_one_is_not(self, urlopen, predict):
        self.feed_down(urlopen)
        three_off_path_polls()

        response = self.client.get(reverse("stop", args=[10]))

        rows = {row.route_id: row for row in response.context["board"].rows}
        self.assertTrue(rows[1].detoured)
        self.assertFalse(rows[2].detoured)
        self.assertContains(response, "possible detour")

    def test_no_detection_no_badge(self, urlopen, predict):
        self.feed_down(urlopen)

        response = self.client.get(reverse("stop", args=[10]))

        self.assertFalse(any(row.detoured for row in response.context["board"].rows))
        self.assertNotContains(response, "possible detour")


@mock.patch("transit.downtowner.urlopen")
class HomeFirstPaintTests(TestCase):
    """The home page's first paint shows a detour already detected in the last
    minute and never touches the feed (#13, #55); the page's swap-in to the
    full alert list detects the rest."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_a_detected_detour_shows_without_calling_the_feed(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)
        three_off_path_polls()
        detect.detoured_routes()  # what the swap-in did a moment ago
        urlopen.reset_mock()

        response = self.client.get(reverse("home"))

        urlopen.assert_not_called()
        self.assertContains(response, "Blue - Weekday Daytime: possible detour")
        self.assertContains(response, "Detected from the bus positions we record")

    def test_a_detour_nobody_has_detected_yet_waits_for_the_swap_in(self, urlopen):
        three_off_path_polls()  # the positions are there, but nothing computed

        response = self.client.get(reverse("home"))

        urlopen.assert_not_called()
        self.assertNotContains(response, "possible detour")


class DemoTests(TestCase):
    """The demo detour alert (#46, #55) is created once and never fights an
    admin who has since edited it."""

    def seed(self):
        out = io.StringIO()
        demo.seed({"admin": None, "rider": None}, out)
        return out.getvalue()

    def test_it_is_created_once(self):
        self.assertIn("Created", self.seed())
        self.assertIn("Kept", self.seed())
        self.assertEqual(Alert.objects.count(), 1)

    def test_it_names_the_stops_it_skips_and_shows_now(self):
        self.seed()
        alert = Alert.objects.get()
        self.assertIn("Chapel St at York St", alert.skipped_stops)
        self.assertTrue(alert.starts_at <= timezone.now() < alert.ends_at)

    def test_an_admin_edit_survives_a_reseed(self):
        self.seed()
        Alert.objects.update(message="Edited by an admin since.")
        self.seed()
        self.assertEqual(Alert.objects.get().message, "Edited by an admin since.")
