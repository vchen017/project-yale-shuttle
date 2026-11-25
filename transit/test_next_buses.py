"""The next-buses page (#24, story #5). The Downtowner feed is mocked at
transit.downtowner.urlopen, answering by endpoint, so no test reaches the
network. Our predictions are mocked where a test needs a particular one."""

import json
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlsplit

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from history.models import Calibration
from history.predictions import countdown_bucket

ROUTES = [
    {"id": 1, "name": "Blue - Weekday Daytime", "short_name": "BD", "color": "4472C4", "active": True,
     "stops": [10, 20, 30]},
    {"id": 2, "name": "Orange", "short_name": "O", "color": "F5A623", "active": True, "stops": [10, 40]},
    {"id": 3, "name": "Red", "short_name": "R", "color": "C00000", "active": False, "stops": [10, 20]},
]
STOPS = [
    {"id": 10, "name": "Science Hill", "lat": 41.316, "lon": -72.922, "description": ""},
    {"id": 20, "name": "Grove Street", "lat": 41.313, "lon": -72.925, "description": ""},
    {"id": 30, "name": "Union Station", "lat": 41.297, "lon": -72.926, "description": ""},
    {"id": 40, "name": "Payne Whitney", "lat": 41.313, "lon": -72.930, "description": ""},
    {"id": 99, "name": "Old Stop", "lat": 41.300, "lon": -72.900, "description": ""},
]
ETAS_AT_SCIENCE_HILL = {
    "etas": {"10": {"etas": [
        {"route": 1, "bus_id": 812, "bus_name": "812", "avg": 9},
        {"route": 1, "bus_id": 815, "bus_name": "815", "avg": 2},
        {"route": 1, "bus_id": 818, "bus_name": "818", "avg": 17},
        {"route": 1, "bus_id": 819, "bus_name": "819", "avg": 31},
    ]}},
}


def feed(routes=ROUTES, stops=STOPS, etas=ETAS_AT_SCIENCE_HILL, buses=()):
    """A urlopen stand-in that answers each feed endpoint. `buses` is the
    buses answer, or None to make that one endpoint fail."""
    answers = {
        "/routes_routes.php": routes,
        "/routes_stops.php": stops,
        "/routes_eta.php": etas,
        "/routes_buses.php": list(buses) if buses is not None else None,
    }

    def urlopen(request, timeout):
        if answers[urlsplit(request.full_url).path] is None:
            raise URLError("buses feed down")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(answers[urlsplit(request.full_url).path]).encode()
        return response

    return urlopen


@mock.patch("transit.downtowner.urlopen")
class NextBusesPageTests(TestCase):
    def setUp(self):
        cache.clear()

    def get(self, stop_id, **query):
        return self.client.get(reverse("stop", args=[stop_id]), query)

    @mock.patch("transit.next_buses.predict_minutes", return_value=4.4)
    def test_lists_each_route_with_its_colour_official_eta_and_our_prediction(self, predict, urlopen):
        urlopen.side_effect = feed()

        response = self.get(10)

        self.assertContains(response, "Next buses at Science Hill")
        rows = response.context["board"].rows
        self.assertEqual([row.route_id for row in rows], [1, 2])  # Red isn't running
        blue = rows[0]
        self.assertEqual(blue.official, [2, 9, 17, 31])
        self.assertEqual(blue.ours, 4)
        self.assertContains(response, "Blue - Weekday Daytime")
        self.assertContains(response, "background-color: #4472C4; color: #fff;")
        self.assertContains(response, "background-color: #F5A623; color: #212529;")
        self.assertContains(response, "2 min")
        self.assertContains(response, "then 9, 17 min")
        self.assertContains(response, ">Our prediction</div>", count=2)
        # All the rows' served checks share one stop-list read (current_by_route).
        predict.assert_any_call(1, 10, current_by_route=mock.ANY)

    @mock.patch("transit.next_buses.predict_minutes", return_value=None)
    def test_without_a_prediction_only_the_official_time_shows_labelled_as_such(self, predict, urlopen):
        urlopen.side_effect = feed()

        response = self.get(10)

        self.assertContains(response, "Official time only")
        self.assertNotContains(response, ">Our prediction</div>")
        self.assertContains(response, "We don't have a prediction for this route at this stop yet.")

    @mock.patch("transit.next_buses.predict_minutes", return_value=None)
    def test_a_destination_keeps_only_routes_that_serve_both_stops(self, predict, urlopen):
        urlopen.side_effect = feed()

        response = self.get(10, to=40)

        self.assertContains(response, "To Payne Whitney")
        self.assertEqual([row.route_id for row in response.context["board"].rows], [2])
        self.assertContains(response, "No bus on the way")

    @mock.patch("transit.next_buses.predict_minutes", return_value=None)
    def test_says_so_when_no_running_route_goes_there(self, predict, urlopen):
        urlopen.side_effect = feed()
        response = self.get(30, to=40)
        self.assertContains(response, "No route running now goes from Union Station to Payne Whitney.")

    def test_the_same_stop_as_destination_is_an_error(self, urlopen):
        urlopen.side_effect = feed()
        response = self.get(10, to=10)
        self.assertContains(response, "Pick a different stop from where you start.")
        self.assertIsNone(response.context["destination"])

    def test_late_at_night_it_says_no_shuttles_are_running(self, urlopen):
        urlopen.side_effect = feed(routes=[dict(route, active=False) for route in ROUTES])
        self.assertContains(self.get(10), "No shuttles are running right now.")

    def test_a_stop_no_running_route_serves_still_has_a_page(self, urlopen):
        urlopen.side_effect = feed()
        self.assertContains(self.get(99), "No route running now stops here.")

    def test_an_unknown_stop_is_not_found(self, urlopen):
        urlopen.side_effect = feed()
        self.assertEqual(self.get(12345).status_code, 404)

    def test_when_the_feed_is_down_the_page_says_so(self, urlopen):
        urlopen.side_effect = URLError("timed out")

        response = self.get(10, to=40)

        self.assertContains(response, "Live shuttle times are unavailable right now.", status_code=503)
        self.assertContains(response, 'href="/stops/10/?to=40"', status_code=503)

    def test_an_answer_that_isnt_the_shape_we_expect_says_the_same_thing(self, urlopen):
        # Readable JSON but not a list: an empty board would tell riders no
        # shuttle is running, which a junk answer doesn't prove (#54's review).
        urlopen.side_effect = feed(routes={"error": "Bad Gateway"}, stops={"error": "Bad Gateway"})

        response = self.get(10)

        self.assertContains(response, "Live shuttle times are unavailable right now.", status_code=503)

    def test_the_feed_is_cached_for_30_seconds(self, urlopen):
        urlopen.side_effect = feed()
        self.get(10)
        calls = urlopen.call_count
        self.get(10)
        self.assertEqual(urlopen.call_count, calls)
        self.assertEqual({call.kwargs["timeout"] for call in urlopen.call_args_list}, {5})

    def test_our_prediction_is_the_feeds_countdown_corrected_by_our_history(self, urlopen):
        # Unmocked predict_minutes (#153). Blue's soonest bus is 9 minutes
        # out, and the recorded history says Blue's countdowns of 5 to 10
        # minutes run 2 minutes short. Orange has no bus in the feed, and
        # the database has no history for it, so it gets no prediction.
        Calibration.objects.create(route_id=1, bucket=countdown_bucket(9), delta_minutes=2.0, samples=40)
        etas = {"etas": {"10": {"etas": [{"route": 1, "bus_id": 812, "bus_name": "812", "avg": 9}]}}}
        urlopen.side_effect = feed(etas=etas)

        rows = {row.route_id: row for row in self.get(10).context["board"].rows}

        self.assertEqual((rows[1].official, rows[1].ours), ([9], 11))
        self.assertIsNone(rows[2].ours)

    def test_a_bus_about_to_come_keeps_the_feeds_countdown(self, urlopen):
        # At 5 minutes or less a countdown is near-perfect and a correction
        # only hurt (#117), so ours is the feed's number.
        Calibration.objects.create(route_id=1, bucket=countdown_bucket(2), delta_minutes=-3.0, samples=40)
        urlopen.side_effect = feed()

        rows = {row.route_id: row for row in self.get(10).context["board"].rows}

        self.assertEqual((rows[1].official[0], rows[1].ours), (2, 2))


NOW = timezone.now()  # the clock these tests run at, so ages are exact


def bus(bus_id, seconds_ago, route=1):
    """A bus from the buses feed whose position is `seconds_ago` old."""
    return {"id": bus_id, "name": str(bus_id), "route": route, "lat": 41.3, "lon": -72.9,
            "heading": 0, "lastStop": 10, "lastUpdate": NOW.timestamp() - seconds_ago}


@mock.patch("transit.next_buses.timezone.now", return_value=NOW)
@mock.patch("transit.next_buses.predict_minutes", return_value=4.4)
@mock.patch("transit.downtowner.urlopen")
class BusAgeTests(TestCase):
    """How old a bus's position is (#58): "last seen N min ago" past 3 minutes,
    and no prediction of ours past 10. Blue's soonest bus here is 815."""

    def setUp(self):
        cache.clear()

    def blue(self, buses):
        response = self.client.get(reverse("stop", args=[10]))
        return response, response.context["board"].rows[0]

    def test_a_bus_seen_within_3_minutes_shows_no_age(self, urlopen, predict, now):
        urlopen.side_effect = feed(buses=[bus(815, 100)])
        response, blue = self.blue(None)
        self.assertIsNone(blue.last_seen)
        self.assertNotContains(response, "last seen")
        self.assertEqual(blue.ours, 4)

    def test_exactly_3_minutes_is_not_yet_old(self, urlopen, predict, now):
        urlopen.side_effect = feed(buses=[bus(815, 180)])
        self.assertNotContains(self.blue(None)[0], "last seen")

    def test_a_bus_seen_5_minutes_ago_says_so_and_keeps_our_prediction(self, urlopen, predict, now):
        urlopen.side_effect = feed(buses=[bus(815, 5 * 60 + 20)])
        response, blue = self.blue(None)
        self.assertContains(response, "last seen 5 min ago", count=1)
        self.assertEqual(blue.ours, 4)
        self.assertContains(response, ">Our prediction</div>", count=2)

    def test_past_10_minutes_our_prediction_is_not_shown(self, urlopen, predict, now):
        urlopen.side_effect = feed(buses=[bus(815, 12 * 60)])
        response, blue = self.blue(None)
        self.assertContains(response, "last seen 12 min ago")
        self.assertIsNone(blue.ours)
        self.assertTrue(blue.ours_hidden)
        self.assertContains(response, "hasn't reported its position in over 10 minutes")
        self.assertContains(response, ">Our prediction</div>", count=1)  # Orange's only
        self.assertEqual(blue.official[0], 2)  # the official time still shows

    def test_exactly_10_minutes_still_shows_our_prediction(self, urlopen, predict, now):
        urlopen.side_effect = feed(buses=[bus(815, 600)])
        response, blue = self.blue(None)
        self.assertEqual(blue.ours, 4)
        self.assertContains(response, "last seen 10 min ago")

    def test_only_the_soonest_bus_counts(self, urlopen, predict, now):
        # 812 (9 min away) froze, but 815 (2 min away) is live.
        urlopen.side_effect = feed(buses=[bus(812, 30 * 60), bus(815, 30)])
        response, blue = self.blue(None)
        self.assertIsNone(blue.last_seen)
        self.assertEqual(blue.ours, 4)

    def test_no_prediction_to_hide_means_the_usual_message(self, urlopen, predict, now):
        predict.return_value = None
        urlopen.side_effect = feed(buses=[bus(815, 12 * 60)])
        response, blue = self.blue(None)
        self.assertFalse(blue.ours_hidden)
        self.assertContains(response, "We don't have a prediction for this route at this stop yet.")

    def test_a_bus_the_buses_feed_doesnt_list_shows_no_age(self, urlopen, predict, now):
        urlopen.side_effect = feed(buses=[bus(999, 3600)])
        response, blue = self.blue(None)
        self.assertIsNone(blue.last_seen)
        self.assertEqual(blue.ours, 4)

    def test_unreadable_update_times_are_ignored(self, urlopen, predict, now):
        broken = [dict(bus(815, 0), lastUpdate=value) for value in (None, "soon", "")]
        for value in broken:
            cache.clear()
            urlopen.side_effect = feed(buses=[value])
            self.assertIsNone(self.blue(None)[1].last_seen)

    def test_when_only_the_buses_feed_is_down_the_page_still_works(self, urlopen, predict, now):
        urlopen.side_effect = feed(buses=None)
        response, blue = self.blue(None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(blue.official[0], 2)
        self.assertEqual(blue.ours, 4)
        self.assertNotContains(response, "last seen")

    def test_a_position_dated_in_the_future_counts_as_fresh(self, urlopen, predict, now):
        urlopen.side_effect = feed(buses=[bus(815, -600)])
        self.assertIsNone(self.blue(None)[1].last_seen)

    def test_the_trip_page_shows_it_too(self, urlopen, predict, now):
        urlopen.side_effect = feed(buses=[bus(815, 6 * 60)])
        response = self.client.get(reverse("stop", args=[10]), {"to": 20})
        self.assertContains(response, "last seen 6 min ago")


@mock.patch("transit.downtowner.urlopen")
class StopPickerTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_asks_where_the_rider_is_starting_and_going(self, urlopen):
        # Typed places, not a long list of stops (story #5): see test_trips.py.
        urlopen.side_effect = feed()
        response = self.client.get(reverse("stops"))
        self.assertContains(response, 'placeholder="Where are you starting?"')
        self.assertContains(response, 'placeholder="Where are you going?"')
        self.assertNotContains(response, "form-select")

    def test_links_that_name_stops_go_to_the_next_buses_page(self, urlopen):
        urlopen.side_effect = feed()
        response = self.client.get(reverse("stops"), {"start": 10, "to": 40})
        self.assertRedirects(response, "/stops/10/?to=40", fetch_redirect_response=False)

    def test_a_link_with_only_a_start_is_enough(self, urlopen):
        urlopen.side_effect = feed()
        response = self.client.get(reverse("stops"), {"start": 10, "to": ""})
        self.assertRedirects(response, "/stops/10/", fetch_redirect_response=False)

    def test_a_link_to_a_stop_not_in_the_list_stays_on_the_search(self, urlopen):
        urlopen.side_effect = feed()
        response = self.client.get(reverse("stops"), {"start": 777})
        self.assertContains(response, 'placeholder="Where are you starting?"')

    def test_when_the_feed_is_down_the_picker_says_so(self, urlopen):
        urlopen.side_effect = URLError("down")
        response = self.client.get(reverse("stops"))
        self.assertContains(response, "Live shuttle times are unavailable right now.", status_code=503)

    def test_the_navbar_links_to_it(self, urlopen):
        self.assertContains(self.client.get("/"), 'href="/stops/"')
        urlopen.assert_not_called()
