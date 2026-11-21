"""Tests for sharing a live trip with a friend (#10). The shuttle feed is
mocked: nothing here reaches the network."""

import datetime as dt
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from places.models import Place
from transit.downtowner import FeedUnavailable

from .models import LIFETIME, TripShare

User = get_user_model()

PATH = [41.300, -72.930, 41.310, -72.930, 41.320, -72.930]
NETWORK = {
    "running": True,
    "routes": [{"id": 1, "name": "Blue", "label": "Blue", "color": "286dc0", "path": PATH}],
    "stops": [
        {"id": 10, "name": "Grove St", "lat": 41.300, "lon": -72.930, "routes": [1], "url": "/stops/10/"},
        {"id": 20, "name": "Science Hill", "lat": 41.320, "lon": -72.930, "routes": [1], "url": "/stops/20/"},
        {"id": 30, "name": "Elsewhere", "lat": 41.200, "lon": -72.900, "routes": [2], "url": "/stops/30/"},
    ],
}
ETAS = [{"route_id": 1, "bus_id": 7, "bus_name": "#7", "minutes": 8}]


def buses(age_seconds=20):
    """The feed's buses answer: bus 7 on the Blue route, between the two stops."""
    return [{"id": 7, "name": "#7", "route": 1, "lat": 41.305, "lon": -72.930, "lastUpdate": time.time() - age_seconds}]


@mock.patch("sharing.views.network", return_value=NETWORK)
class CreateTests(TestCase):
    def setUp(self):
        self.rider = User.objects.create_user("night-rider", password="not-a-real-one")
        self.client.force_login(self.rider)

    def share(self, **changes):
        data = {"route_id": 1, "bus_id": 7, "board_stop_id": 10, "get_off_stop_id": 20, **changes}
        return self.client.post(reverse("share-create"), data)

    def test_sharing_a_trip_makes_a_link_that_lasts_three_hours(self, network):
        response = self.share()
        share = TripShare.objects.get()
        self.assertRedirects(response, reverse("share", args=[share.token]), fetch_redirect_response=False)
        self.assertEqual((share.route_name, share.board_stop_name, share.get_off_stop_name), ("Blue", "Grove St", "Science Hill"))
        self.assertEqual(share.bus_id, 7)
        self.assertEqual(share.expires_at - share.created_at, LIFETIME)
        self.assertGreaterEqual(len(share.token), 32)  # unguessable

    def test_a_bus_the_feed_didnt_name_still_shares_the_route(self, network):
        self.share(bus_id="")
        self.assertIsNone(TripShare.objects.get().bus_id)

    def test_only_a_real_route_and_its_stops_can_be_shared(self, network):
        for bad in ({"route_id": 99}, {"get_off_stop_id": 30}, {"board_stop_id": "x"}, {"route_id": ""}):
            with self.subTest(bad=bad):
                self.assertEqual(self.share(**bad).status_code, 400)
        self.assertFalse(TripShare.objects.exists())

    def test_a_new_share_ends_the_last_one(self, network):
        self.share()
        self.share()
        first, second = TripShare.objects.order_by("created_at", "id")
        self.assertIsNotNone(first.stopped_at)
        self.assertIsNone(second.stopped_at)

    def test_sharing_needs_a_log_in(self, network):
        self.client.logout()
        response = self.share()
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.assertFalse(TripShare.objects.exists())

    def test_with_the_feed_down_it_says_so_and_shares_nothing(self, network):
        network.side_effect = FeedUnavailable("down")
        response = self.share()
        self.assertRedirects(response, reverse("stops"), fetch_redirect_response=False)
        self.assertFalse(TripShare.objects.exists())


@mock.patch("sharing.views.downtowner.etas", return_value=ETAS)
@mock.patch("sharing.views.downtowner.buses", side_effect=buses)
@mock.patch("sharing.views.network", return_value=NETWORK)
class SharedTripTests(TestCase):
    def setUp(self):
        self.rider = User.objects.create_user("night-rider", password="not-a-real-one")
        Place.objects.create(
            user=self.rider, name="My dorm", address="Old Campus, New Haven", lat=41.30, lon=-72.93, is_home=True
        )
        self.share = TripShare.objects.create(
            user=self.rider, route_id=1, route_name="Blue", route_color="286dc0", bus_id=7,
            board_stop_id=10, board_stop_name="Grove St", get_off_stop_id=20, get_off_stop_name="Science Hill",
        )
        self.url = reverse("share", args=[self.share.token])

    def test_a_friend_sees_the_bus_and_when_it_arrives_without_logging_in(self, network, buses_, etas):
        response = self.client.get(self.url)
        self.assertContains(response, "The Blue bus reaches Science Hill in 8 min")
        self.assertContains(response, "updates every 30 seconds")
        self.assertContains(response, 'data-share-live data-refresh-seconds="30"')  # share.js keeps it fresh
        self.assertContains(response, '<noscript><meta http-equiv="refresh" content="30"></noscript>')
        live = response.context["live"]
        self.assertEqual(live["map"]["bus"], {"at": [41.305, -72.93]})
        self.assertEqual(live["map"]["get_off"]["name"], "Science Hill")
        self.assertGreater(len(live["map"]["route"]["path"]), 1)
        etas.assert_called_once_with(20)  # timed at the stop where the rider gets off

    def test_nothing_about_the_rider_shows(self, network, buses_, etas):
        response = self.client.get(self.url)
        page = response.content.decode()
        for private in ("night-rider", "My dorm", "Old Campus"):
            self.assertNotIn(private, page)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_the_link_ends_after_three_hours(self, network, buses_, etas):
        with mock.patch("sharing.views.timezone.now", return_value=self.share.created_at + LIFETIME - dt.timedelta(seconds=1)):
            self.assertEqual(self.client.get(self.url).status_code, 200)
        with mock.patch("sharing.views.timezone.now", return_value=self.share.created_at + LIFETIME):
            response = self.client.get(self.url)
        self.assertContains(response, "This trip isn't being shared anymore", status_code=410)
        self.assertNotContains(response, "Science Hill", status_code=410)

    def test_stopping_ends_the_link_at_once(self, network, buses_, etas):
        self.client.force_login(self.rider)
        response = self.client.post(reverse("share-stop", args=[self.share.token]))
        self.assertRedirects(response, self.url, target_status_code=410)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 410)

    def test_only_the_rider_who_shared_it_can_stop_it(self, network, buses_, etas):
        self.client.force_login(User.objects.create_user("someone-else", password="not-it"))
        self.assertEqual(self.client.post(reverse("share-stop", args=[self.share.token])).status_code, 404)
        self.share.refresh_from_db()
        self.assertIsNone(self.share.stopped_at)

    def test_the_sharer_gets_the_link_to_copy_and_a_way_to_stop(self, network, buses_, etas):
        self.client.force_login(self.rider)
        response = self.client.get(self.url)
        self.assertContains(response, "Send this link to a friend")
        self.assertContains(response, f'value="http://testserver{self.url}"')
        self.assertContains(response, reverse("share-stop", args=[self.share.token]))
        self.assertNotContains(response, 'http-equiv="refresh"')  # their page holds still while they copy

    def test_an_unknown_link_is_a_404(self, network, buses_, etas):
        self.assertEqual(self.client.get(reverse("share", args=["no-such-link"])).status_code, 404)

    def test_with_the_feed_down_the_page_still_answers(self, network, buses_, etas):
        buses_.side_effect = FeedUnavailable("down")
        response = self.client.get(self.url)
        self.assertContains(response, "Live shuttle times are unavailable right now")

    def test_a_bus_gone_from_the_map_and_the_times_says_so(self, network, buses_, etas):
        buses_.side_effect = None
        buses_.return_value = []
        etas.return_value = []
        response = self.client.get(self.url)
        self.assertContains(response, "No arrival time at Science Hill right now")
        self.assertContains(response, "The bus isn't on the shuttle map right now")
        self.assertIsNone(response.context["live"]["map"]["bus"])

    def test_a_stale_position_says_how_old_it_is(self, network, buses_, etas):
        buses_.side_effect = lambda: buses(age_seconds=600)
        self.assertContains(self.client.get(self.url), "last reported where it is 10 min ago")

