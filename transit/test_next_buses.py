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


def feed(routes=ROUTES, stops=STOPS, etas=ETAS_AT_SCIENCE_HILL):
    """A urlopen stand-in that answers each feed endpoint."""
    answers = {
        "/routes_routes.php": routes,
        "/routes_stops.php": stops,
        "/routes_eta.php": etas,
    }

    def urlopen(request, timeout):
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
        predict.assert_any_call(1, 10, use_live_feed=False)

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

    def test_the_feed_is_cached_for_30_seconds(self, urlopen):
        urlopen.side_effect = feed()
        self.get(10)
        calls = urlopen.call_count
        self.get(10)
        self.assertEqual(urlopen.call_count, calls)
        self.assertEqual({call.kwargs["timeout"] for call in urlopen.call_args_list}, {5})

    def test_our_prediction_never_just_repeats_the_official_time(self, urlopen):
        # Unmocked predict_minutes, empty database: the feed has Blue's next
        # bus in 2 minutes, but our column comes from our own recorded data,
        # which has nothing yet, so neither route gets a prediction.
        urlopen.side_effect = feed()

        rows = {row.route_id: row for row in self.get(10).context["board"].rows}

        self.assertEqual(rows[1].official[0], 2)
        self.assertIsNone(rows[1].ours)
        self.assertIsNone(rows[2].ours)


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
