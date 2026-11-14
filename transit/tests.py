import io
import json
import socket
from unittest import mock
from urllib.error import HTTPError, URLError

from django.core.cache import cache
from django.test import SimpleTestCase

from transit import downtowner


def answer(data):
    """What urlopen returns for a feed that answers with this JSON."""
    return io.BytesIO(json.dumps(data).encode())


ROUTES = [
    {"id": 1, "name": "Blue - Weekday Daytime", "short_name": "BD", "active": True, "stops": [5, 7]},
    {"id": 16, "name": "Blue West", "short_name": "BW", "active": False, "stops": [7]},
]

ETAS = {
    "calculation_time": 1763112000,
    "etas": {
        "1": {"etas": [
            {"route": 1, "bus_id": 812, "bus_name": "812", "avg": 9},
            {"route": 1, "bus_id": 815, "bus_name": "815", "avg": 2},
        ]},
        "3": {"etas": [{"route": "3", "bus_id": "901", "bus_name": "901", "avg": 5}]},
    },
}


@mock.patch("transit.downtowner.urlopen")
class FeedTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_routes_come_from_the_feed(self, urlopen):
        urlopen.return_value = answer(ROUTES)
        self.assertEqual(downtowner.routes(), ROUTES)
        url = urlopen.call_args.args[0].full_url
        self.assertEqual(url, "https://yale.downtownerapp.com/routes_routes.php?inactive=true")

    def test_each_feed_reads_its_own_endpoint(self, urlopen):
        urlopen.side_effect = lambda *args, **kwargs: answer([])
        downtowner.stops()
        downtowner.buses()
        downtowner.announcements()
        urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertEqual(urls, [
            "https://yale.downtownerapp.com/routes_stops.php",
            "https://yale.downtownerapp.com/routes_buses.php",
            "https://yale.downtownerapp.com/routes_announcements.php",
        ])

    def test_a_second_call_within_30_seconds_uses_the_cache(self, urlopen):
        urlopen.return_value = answer(ROUTES)
        with mock.patch.object(downtowner.cache, "set", wraps=downtowner.cache.set) as cache_set:
            downtowner.routes()
            downtowner.routes()
        self.assertEqual(urlopen.call_count, 1)
        cache_set.assert_called_once_with(
            "downtowner:routes_routes.php?inactive=true", ("ok", ROUTES), 30
        )

    def test_requests_time_out_after_5_seconds(self, urlopen):
        urlopen.return_value = answer(ROUTES)
        downtowner.routes()
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 5)

    def test_etas_are_flattened_and_soonest_first(self, urlopen):
        urlopen.return_value = answer(ETAS)
        self.assertEqual(downtowner.etas(7), [
            {"route_id": 1, "bus_id": 815, "bus_name": "815", "minutes": 2},
            {"route_id": 3, "bus_id": 901, "bus_name": "901", "minutes": 5},
            {"route_id": 1, "bus_id": 812, "bus_name": "812", "minutes": 9},
        ])
        self.assertTrue(urlopen.call_args.args[0].full_url.endswith("/routes_eta.php?stop=7"))

    def test_each_stop_is_cached_separately(self, urlopen):
        urlopen.side_effect = lambda *args, **kwargs: answer(ETAS)
        downtowner.etas(7)
        downtowner.etas(8)
        downtowner.etas(7)
        self.assertEqual(urlopen.call_count, 2)

    def test_etas_skip_entries_without_a_route_or_time(self, urlopen):
        urlopen.return_value = answer({"etas": {"1": {"etas": [
            {"route": 1, "bus_id": 812, "avg": None},
            {"bus_id": 813, "avg": 4},
            {"route": 1, "bus_id": None, "bus_name": "", "avg": 6},
        ]}}})
        self.assertEqual(downtowner.etas(7), [
            {"route_id": 1, "bus_id": None, "bus_name": "", "minutes": 6},
        ])

    def test_a_stop_with_no_etas_gives_an_empty_list(self, urlopen):
        urlopen.return_value = answer([])
        self.assertEqual(downtowner.etas(7), [])

    def test_no_announcements_gives_an_empty_list(self, urlopen):
        urlopen.return_value = answer(None)
        self.assertEqual(downtowner.announcements(), [])

    def test_a_timeout_raises_feed_unavailable(self, urlopen):
        urlopen.side_effect = socket.timeout("timed out")
        with self.assertLogs("transit.downtowner", "WARNING"), self.assertRaises(downtowner.FeedUnavailable):
            downtowner.routes()

    def test_a_server_error_raises_feed_unavailable(self, urlopen):
        urlopen.side_effect = HTTPError("https://yale.downtownerapp.com", 503, "Unavailable", {}, None)
        with self.assertLogs("transit.downtowner", "WARNING"), self.assertRaises(downtowner.FeedUnavailable):
            downtowner.stops()

    def test_no_connection_raises_feed_unavailable(self, urlopen):
        urlopen.side_effect = URLError("Name or service not known")
        with self.assertLogs("transit.downtowner", "WARNING"), self.assertRaises(downtowner.FeedUnavailable):
            downtowner.etas(7)

    def test_an_answer_that_isnt_json_raises_feed_unavailable(self, urlopen):
        urlopen.return_value = io.BytesIO(b"<html>Maintenance</html>")
        with self.assertLogs("transit.downtowner", "WARNING"), self.assertRaises(downtowner.FeedUnavailable):
            downtowner.buses()

    def test_an_outage_is_remembered_briefly_instead_of_retried_every_call(self, urlopen):
        urlopen.side_effect = socket.timeout("timed out")
        with mock.patch.object(downtowner.cache, "set", wraps=downtowner.cache.set) as cache_set:
            with self.assertLogs("transit.downtowner", "WARNING") as logs:
                for _ in range(3):
                    with self.assertRaises(downtowner.FeedUnavailable):
                        downtowner.routes()
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(cache_set.call_args.args[2], 10)

    def test_the_feed_is_used_again_once_it_answers(self, urlopen):
        urlopen.side_effect = [socket.timeout("timed out"), answer(ROUTES)]
        with self.assertLogs("transit.downtowner", "WARNING"), self.assertRaises(downtowner.FeedUnavailable):
            downtowner.routes()
        cache.clear()  # the 10-second outage memory has passed
        self.assertEqual(downtowner.routes(), ROUTES)

    def test_the_feed_address_comes_from_settings(self, urlopen):
        urlopen.return_value = answer([])
        with self.settings(DOWNTOWNER_URL="https://feed.example.com/"):
            downtowner.stops()
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://feed.example.com/routes_stops.php")
