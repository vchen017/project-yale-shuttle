"""The trip search (story #5): the places riders type, the stops a trip uses,
and the trip page. Photon is mocked at transit.places.urlopen and the
Downtowner feed at transit.downtowner.urlopen, so no test reaches the network.
In its own file, so it doesn't collide with other pull requests."""

import json
from unittest import mock
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import places, trips
from .test_next_buses import feed

PHOTON = {
    "features": [
        {
            "geometry": {"coordinates": [-72.9290, 41.3112]},
            "properties": {"name": "Sterling Library", "street": "York Street", "city": "New Haven"},
        },
        {
            "geometry": {"coordinates": [-72.9289, 41.3115]},
            "properties": {"housenumber": "120", "street": "High Street", "city": "New Haven"},
        },
        # One station three times: OpenStreetMap has the station, its building and a platform.
        {
            "geometry": {"coordinates": [-72.9266, 41.2975]},
            "properties": {"name": "New Haven Union Station", "housenumber": "50", "street": "Union Avenue", "city": "New Haven"},
        },
        {
            "geometry": {"coordinates": [-72.9265, 41.2976]},
            "properties": {"name": "New Haven Union Station", "street": "Union Avenue", "city": "New Haven"},
        },
        {
            "geometry": {"coordinates": [-72.9251, 41.2984]},
            "properties": {"name": "New Haven Union Station", "street": "Union Avenue", "city": "New Haven"},
        },
    ]
}

# The served stops, as next_buses.served_stops() gives them.
SCIENCE_HILL = {"id": 10, "name": "Science Hill", "lat": 41.316, "lon": -72.922, "routes": [1, 2], "url": "/stops/10/"}
GROVE = {"id": 20, "name": "Grove Street", "lat": 41.313, "lon": -72.925, "routes": [1], "url": "/stops/20/"}
UNION = {"id": 30, "name": "Union Station", "lat": 41.297, "lon": -72.926, "routes": [1], "url": "/stops/30/"}
PAYNE_WHITNEY = {"id": 40, "name": "Payne Whitney", "lat": 41.313, "lon": -72.930, "routes": [2], "url": "/stops/40/"}
SERVED = [GROVE, PAYNE_WHITNEY, SCIENCE_HILL, UNION]


def photon(answer=PHOTON):
    """A urlopen stand-in for Photon."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(answer).encode()
    return mock.Mock(return_value=response)


# These test how Photon's answers are handled, so our own buildings (#98, see
# buildings/tests.py) are switched off here.
@mock.patch("transit.places.find_buildings", new=lambda query: [])
@mock.patch("transit.places.urlopen", new_callable=photon)
class PlaceSearchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_our_stops_come_first_then_buildings_and_addresses(self, urlopen):
        found = places.search("union", SERVED)
        self.assertEqual(
            [(place["name"], place["detail"]) for place in found],
            [
                ("Union Station", "Shuttle stop"),
                ("Sterling Library", "York Street, New Haven"),
                ("120 High Street", "New Haven"),
                ("New Haven Union Station", "50 Union Avenue, New Haven"),
            ],
        )
        self.assertEqual((found[1]["lat"], found[1]["lon"]), (41.3112, -72.929))

    def test_a_street_in_pieces_is_one_place(self, urlopen):
        street = {"name": "Chapel Street", "city": "New Haven"}
        urlopen.return_value.read.return_value = json.dumps({"features": [
            {"geometry": {"coordinates": [-72.93, 41.307]}, "properties": street},
            {"geometry": {"coordinates": [-72.95, 41.316]}, "properties": street},
        ]}).encode()
        self.assertEqual([place["name"] for place in places.search("chapel", SERVED)], ["Chapel Street"])

    def test_a_stop_matches_on_every_word_in_any_order(self, urlopen):
        self.assertEqual(places.search("hill science", SERVED)[0]["name"], "Science Hill")
        self.assertNotIn("Grove Street", [place["name"] for place in places.search("grove hill", SERVED)])

    def test_photon_is_asked_about_new_haven_only(self, urlopen):
        places.search("  sterling   library ", SERVED)
        request = urlopen.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(request.full_url.split("?")[0], "https://photon.komoot.io/api/")
        self.assertEqual(query["q"], ["sterling library"])
        self.assertEqual(query["bbox"], ["-73.05,41.24,-72.83,41.40"])
        self.assertEqual((query["lat"], query["lon"], query["zoom"]), (["41.3111"], ["-72.9267"], ["14"]))
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 3)

    def test_each_answer_is_cached(self, urlopen):
        places.search("sterling", SERVED)
        places.search("Sterling", SERVED)
        self.assertEqual(urlopen.call_count, 1)

    def test_when_photon_is_down_our_stops_still_come_back(self, urlopen):
        urlopen.side_effect = URLError("down")
        with self.assertLogs("transit.places", "WARNING"):
            self.assertEqual([place["name"] for place in places.search("union", SERVED)], ["Union Station"])
        urlopen.side_effect = None
        places.search("union", SERVED)  # not cached while it was down: asked again
        self.assertEqual(urlopen.call_count, 2)

    def test_an_answer_we_cant_read_gives_no_places(self, urlopen):
        urlopen.return_value.read.return_value = b'{"features": [{"geometry": {}}]}'
        with self.assertLogs("transit.places", "WARNING"):
            self.assertEqual(places.search("sterling", SERVED), [])

    def test_a_single_letter_asks_nobody(self, urlopen):
        self.assertEqual(places.search(" s ", SERVED), [])
        urlopen.assert_not_called()


class TripPlanTests(SimpleTestCase):
    def test_the_stops_on_a_shared_route_with_the_least_walking(self):
        plan = trips.plan((41.3140, -72.9260), (41.2960, -72.9270), SERVED)
        self.assertEqual((plan.board_at["name"], plan.get_off_at["name"]), ("Grove Street", "Union Station"))
        self.assertEqual((plan.walk_to_stop, plan.walk_from_stop), (2, 2))  # 139 m each
        self.assertEqual(plan.walk_all_the_way, 25)
        self.assertFalse(plan.walking_is_quicker)

    def test_a_place_at_the_stop_needs_no_walk(self):
        plan = trips.plan((41.3131, -72.9251), (41.2971, -72.9261), SERVED)  # 14 m from each stop
        self.assertEqual((plan.walk_to_stop, plan.walk_from_stop), (0, 0))

    def test_a_nearer_stop_on_another_route_loses_to_one_on_a_shared_route(self):
        # Payne Whitney is the nearest stop to the start, but only the Orange
        # serves it, and the Orange doesn't go to Union Station.
        plan = trips.plan((41.3130, -72.9298), (41.2971, -72.9261), SERVED)
        self.assertEqual((plan.board_at["name"], plan.get_off_at["name"]), ("Grove Street", "Union Station"))

    def test_no_shared_route_means_no_plan(self):
        self.assertIsNone(trips.plan((41.3130, -72.9298), (41.2971, -72.9261), [PAYNE_WHITNEY, UNION]))

    def test_walking_is_quicker_for_a_short_trip(self):
        plan = trips.plan((41.3131, -72.9251), (41.3135, -72.9290), SERVED)
        self.assertTrue(plan.walking_is_quicker)


@mock.patch("transit.next_buses.predict_minutes", return_value=None)
@mock.patch("transit.places.urlopen", new_callable=photon)
@mock.patch("transit.downtowner.urlopen")
class TripPageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_picked_places_get_the_walks_and_the_next_buses(self, feed_urlopen, photon_urlopen, predict):
        feed_urlopen.side_effect = feed()
        response = self.client.get(
            reverse("trip"),
            {"from": "Grove and High", "from_at": "41.3140,-72.9260", "to": "The station", "to_at": "41.2960,-72.9270"},
        )
        self.assertContains(response, "Grove and High to The station")
        self.assertContains(response, 'Walk 2 min to the <a href="/stops/20/">Grove Street</a> stop', html=False)
        self.assertContains(response, "Take the shuttle to <strong>Union Station</strong>", html=False)
        self.assertContains(response, "Walk 2 min to The station")
        self.assertContains(response, "Blue - Weekday Daytime")
        self.assertContains(response, "Walking the whole way takes about 25 min.")
        photon_urlopen.assert_not_called()  # picked places need no search

    def test_typed_places_are_looked_up(self, feed_urlopen, photon_urlopen, predict):
        feed_urlopen.side_effect = feed()
        response = self.client.get(reverse("trip"), {"from": "grove", "to": "union"})
        self.assertContains(response, "Grove Street to Union Station")
        self.assertEqual(response.context["plan"].board_at["id"], 20)
        # Stop to stop: no walk at either end.
        self.assertContains(response, 'Start at the <a href="/stops/20/">Grove Street</a> stop', html=False)
        self.assertNotContains(response, "Walk 0 min")

    def test_a_place_we_cant_find_says_so_and_keeps_what_was_typed(self, feed_urlopen, photon_urlopen, predict):
        feed_urlopen.side_effect = feed()
        photon_urlopen.return_value.read.return_value = b'{"features": []}'
        response = self.client.get(reverse("trip"), {"from": "Nowhere Lane", "to": "union"})
        self.assertContains(
            response,
            "We couldn't find “Nowhere Lane” near New Haven. Try a building, a street address or a stop.",
            html=True,
        )
        self.assertContains(response, 'value="Nowhere Lane"')
        self.assertContains(response, 'value="Union Station"')

    def test_an_empty_search_asks_for_both_places(self, feed_urlopen, photon_urlopen, predict):
        feed_urlopen.side_effect = feed()
        response = self.client.get(reverse("trip"))
        self.assertContains(response, "Type where you're starting.", html=True)
        self.assertContains(response, "Type where you're going.", html=True)

    def test_coordinates_that_arent_a_place_are_ignored(self, feed_urlopen, photon_urlopen, predict):
        feed_urlopen.side_effect = feed()
        response = self.client.get(reverse("trip"), {"from": "grove", "from_at": "999,0", "to": "union", "to_at": "nan,1"})
        self.assertEqual(response.context["plan"].board_at["id"], 20)

    def test_when_no_route_links_the_places_it_says_so(self, feed_urlopen, photon_urlopen, predict):
        feed_urlopen.side_effect = feed()
        far_away = {"from": "Boston", "from_at": "42.3601,-71.0589", "to": "NYC", "to_at": "40.7128,-74.0060"}
        with mock.patch("transit.trips.plan", return_value=None):
            response = self.client.get(reverse("trip"), far_away)
        self.assertContains(response, "No shuttle route running now links stops near these two places.")

    def test_when_the_feed_is_down_the_page_says_so(self, feed_urlopen, photon_urlopen, predict):
        feed_urlopen.side_effect = URLError("down")
        response = self.client.get(reverse("trip"), {"from": "a", "from_at": "41.31,-72.92", "to": "b", "to_at": "41.30,-72.93"})
        self.assertContains(response, "Live shuttle times are unavailable right now.", status_code=503)


@mock.patch("transit.places.urlopen", new_callable=photon)
@mock.patch("transit.downtowner.urlopen")
class PlacesJsonTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_suggests_our_stops_and_leaves_addresses_to_the_browser(self, feed_urlopen, photon_urlopen):
        feed_urlopen.side_effect = feed()
        response = self.client.get(reverse("places"), {"q": " UNION  "})
        self.assertEqual([place["name"] for place in response.json()["places"]], ["Union Station"])
        self.assertEqual(response["Cache-Control"], "max-age=300")
        photon_urlopen.assert_not_called()  # Photon refuses our servers: the browser asks it

    def test_a_single_letter_suggests_nothing(self, feed_urlopen, photon_urlopen):
        feed_urlopen.side_effect = feed()
        self.assertEqual(self.client.get(reverse("places"), {"q": "u"}).json()["places"], [])

    def test_when_the_feed_is_down_it_suggests_no_stops_instead_of_failing(self, feed_urlopen, photon_urlopen):
        feed_urlopen.side_effect = URLError("down")
        response = self.client.get(reverse("places"), {"q": "union"})
        self.assertEqual((response.status_code, response.json()["places"]), (200, []))


@mock.patch("transit.downtowner.urlopen")
class SearchBoxTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_the_home_page_has_the_search_and_doesnt_wait_on_the_feed(self, urlopen):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'placeholder="Where are you starting?"')
        self.assertContains(response, 'placeholder="Where are you going?"')
        self.assertContains(response, "core/trip-search.js")
        urlopen.assert_not_called()

    def test_a_stop_page_starts_the_search_from_that_stop(self, urlopen):
        urlopen.side_effect = feed()
        with mock.patch("transit.next_buses.predict_minutes", return_value=None):
            response = self.client.get(reverse("stop", args=[10]), {"to": 40})
        self.assertContains(response, 'value="Science Hill"')
        self.assertContains(response, 'value="Payne Whitney"')
        # Only the script sends coordinates; without it, the server looks the names up.
        self.assertContains(response, 'name="from_at" disabled', html=False)
        self.assertContains(response, 'value="41.316000,-72.922000"')
