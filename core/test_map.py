"""Tests for the home page's map of stops and its data (#71)."""

import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from transit.downtowner import FeedUnavailable

NETWORK = {
    "running": True,
    "routes": [{"id": 1, "name": "Blue", "label": "Blue", "color": "4472C4", "path": [41.3, -72.9, 41.31, -72.92]}],
    "stops": [{"id": 10, "name": "Science Hill", "lat": 41.316, "lon": -72.922, "routes": [1], "url": None}],
}


class HomeMapTests(TestCase):
    @mock.patch("transit.downtowner._get", side_effect=AssertionError("the home page called the feed"))
    def test_the_home_page_has_the_map_and_never_waits_on_the_feed(self, feed):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Shuttle stops near you")
        self.assertContains(response, f'data-network-url="{reverse("network")}"')
        self.assertContains(response, "leaflet@1.9.4/dist/leaflet.js")
        self.assertContains(response, 'integrity="sha384-')
        self.assertContains(response, "core/map.js")
        feed.assert_not_called()

    @mock.patch("core.views.network", return_value=NETWORK)
    def test_the_map_data(self, network):
        response = self.client.get(reverse("network"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), NETWORK)
        self.assertEqual(response["Cache-Control"], "max-age=30")

    @mock.patch("core.views.network", side_effect=FeedUnavailable("timed out"))
    def test_when_the_feed_is_down_the_map_data_says_so(self, network):
        with self.assertLogs("django.request", "ERROR"):  # Django logs every 503
            response = self.client.get(reverse("network"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content), {"error": "Live shuttle data is unavailable right now."})
