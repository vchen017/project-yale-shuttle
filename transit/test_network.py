"""Tests for the map's view of the shuttle network (#71). In their own file, so
they don't collide with the next-buses page's tests in transit/test_next_buses.py."""

from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.urls import path

from transit import network

# A URL list without the next-buses page, to check stops then link nowhere.
urlpatterns = [path("elsewhere/", lambda request: None, name="elsewhere")]

ROUTES = [
    {"id": 1, "name": "Blue - Weekday Daytime", "color": "4472C4", "active": True, "stops": [10, 11], "path": [41.3, -72.9, 41.31, -72.92]},
    {"id": 2, "name": "Orange", "color": "not a colour", "active": True, "stops": [11, 12], "path": []},
    {"id": 3, "name": "Night", "color": "000000", "active": False, "stops": [13], "path": [41.3, -72.9]},
]
STOPS = [
    {"id": 10, "name": "Science Hill", "lat": 41.316, "lon": -72.922},
    {"id": 11, "name": "Grove Street", "lat": 41.312, "lon": -72.925},
    {"id": 12, "name": "Broadway", "lat": 41.311, "lon": -72.931},
    {"id": 13, "name": "Night-only stop", "lat": 41.30, "lon": -72.93},
    {"id": 14, "name": "Served by nobody", "lat": 41.29, "lon": -72.94},
    {"id": 15, "name": "No coordinates", "lat": None, "lon": None},
]


@mock.patch("transit.downtowner.stops", return_value=STOPS)
class NetworkTests(SimpleTestCase):
    @mock.patch("transit.downtowner.routes", return_value=ROUTES)
    def test_only_the_routes_running_now_and_the_stops_they_serve(self, routes, stops):
        result = network.network()
        self.assertTrue(result["running"])
        self.assertEqual([route["id"] for route in result["routes"]], [1, 2])
        served = {stop["id"]: stop["routes"] for stop in result["stops"]}
        self.assertEqual(served, {10: [1], 11: [1, 2], 12: [2]})

    @mock.patch("transit.downtowner.routes", return_value=ROUTES)
    def test_route_colours_are_six_hex_digits(self, routes, stops):
        colours = {route["label"]: route["color"] for route in network.network()["routes"]}
        self.assertEqual(colours, {"Blue": "4472C4", "Orange": network.DEFAULT_COLOR})

    @mock.patch("transit.downtowner.routes", return_value=ROUTES)
    def test_chips_get_a_short_label(self, routes, stops):
        blue = network.network()["routes"][0]
        self.assertEqual((blue["name"], blue["label"]), ("Blue - Weekday Daytime", "Blue"))

    @mock.patch("transit.downtowner.routes", return_value=[dict(route, active=False) for route in ROUTES])
    def test_with_nothing_running_it_shows_every_route(self, routes, stops):
        result = network.network()
        self.assertFalse(result["running"])
        self.assertEqual(len(result["routes"]), 3)
        self.assertIn(13, [stop["id"] for stop in result["stops"]])

    @mock.patch("transit.downtowner.routes", return_value=ROUTES)
    def test_stops_link_to_their_next_buses_page(self, routes, stops):
        self.assertEqual(network.network()["stops"][0]["url"], "/stops/10/")
        with override_settings(ROOT_URLCONF="transit.test_network"):
            self.assertEqual({stop["url"] for stop in network.network()["stops"]}, {None})

    @mock.patch("transit.downtowner.routes", side_effect=network.downtowner.FeedUnavailable("down"))
    def test_an_unavailable_feed_raises(self, routes, stops):
        with self.assertRaises(network.downtowner.FeedUnavailable):
            network.network()
