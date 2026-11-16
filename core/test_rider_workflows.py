"""The main rider workflows, end to end through the test client, and what riders
see when the shuttle feed is slow, down or stale (#75, from Kyle's Sprint 1
feedback in #71). Every outside service is mocked: the Downtowner feed at
transit.downtowner.urlopen, push services at notifications.push.webpush.
"""

import base64
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from transit import downtowner

PUSH_KEYS = {"VAPID_PUBLIC_KEY": "test-public-key", "VAPID_PRIVATE_KEY": "test-private-key"}
P256DH = base64.urlsafe_b64encode(b"\x04" + bytes(range(64))).rstrip(b"=").decode()
AUTH = base64.urlsafe_b64encode(bytes(range(16))).rstrip(b"=").decode()
SUBSCRIPTION = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/rider-laptop",
    "keys": {"p256dh": P256DH, "auth": AUTH},
}


def feed_answer(data):
    """What urlopen returns for one of the feed's JSON answers."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(data).encode()
    return response


class RiderWorkflowTests(TestCase):
    @override_settings(**PUSH_KEYS)
    @mock.patch("notifications.push.webpush")
    def test_a_new_rider_signs_up_turns_on_notifications_and_logs_out(self, webpush):
        password = "a-rider-password-5"
        response = self.client.post(
            reverse("signup"),
            {"username": "newrider", "email": "newrider@example.com", "password1": password, "password2": password},
            follow=True,
        )
        # Signed up, logged in, and on the home page with the stops map.
        self.assertTrue(response.context["user"].is_authenticated)
        self.assertContains(response, "Shuttle stops near you")
        self.assertContains(response, reverse("notifications"))

        page = self.client.get(reverse("notifications"))
        self.assertContains(page, 'data-public-key="test-public-key"')
        subscribed = self.client.post(reverse("push-subscribe"), json.dumps(SUBSCRIPTION), "application/json")
        self.assertEqual(subscribed.status_code, 201)
        self.assertContains(self.client.get(reverse("notifications")), "None yet", count=0)

        tested = self.client.post(reverse("push-test"), follow=True)
        self.assertContains(tested, "Sent a test notification to 1 device.")
        self.assertEqual(webpush.call_args.kwargs["subscription_info"]["endpoint"], SUBSCRIPTION["endpoint"])

        self.client.post(reverse("logout"))
        self.assertRedirects(
            self.client.get(reverse("notifications")),
            f"{reverse('login')}?next={reverse('notifications')}",
        )

    def test_a_returning_rider_logs_in_and_lands_on_the_home_page(self):
        get_user_model().objects.create_user("rider2@example.com", password="a-rider-password-6")
        response = self.client.post(
            reverse("login"), {"username": "rider2@example.com", "password": "a-rider-password-6"}, follow=True
        )
        self.assertTrue(response.context["user"].is_authenticated)
        self.assertContains(response, "Shuttle stops near you")

    def test_a_wrong_password_shows_an_error_and_logs_nobody_in(self):
        get_user_model().objects.create_user("rider3@example.com", password="a-rider-password-7")
        response = self.client.post(reverse("login"), {"username": "rider3@example.com", "password": "nope"})
        self.assertFalse(response.context["user"].is_authenticated)
        self.assertContains(response, "correct username and password")


@mock.patch("transit.downtowner.urlopen")
class FeedDownTests(TestCase):
    """What riders see when the Downtowner feed is slow, down or stale."""

    def setUp(self):
        cache.clear()  # the feed's answers are cached; each test starts cold
        self.addCleanup(cache.clear)

    def test_pages_load_while_the_feed_is_down(self, urlopen):
        urlopen.side_effect = TimeoutError("timed out")
        for name in ("home", "login", "signup"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        urlopen.assert_not_called()  # no page waits on the feed

    def test_a_slow_feed_makes_the_map_say_live_data_is_unavailable(self, urlopen):
        urlopen.side_effect = TimeoutError("timed out")
        with self.assertLogs("transit.downtowner", "WARNING"), self.assertLogs("django.request", "ERROR"):
            response = self.client.get(reverse("network"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "Live shuttle data is unavailable right now."})
        self.assertEqual(urlopen.call_args.kwargs["timeout"], downtowner.TIMEOUT_SECONDS)

    def test_an_answer_that_isnt_json_is_treated_as_down(self, urlopen):
        broken = feed_answer({})
        broken.read.return_value = b"<html>Service Unavailable</html>"
        urlopen.return_value = broken
        with self.assertLogs("transit.downtowner", "WARNING"), self.assertLogs("django.request", "ERROR"):
            self.assertEqual(self.client.get(reverse("network")).status_code, 503)

    def test_old_answers_are_never_shown_once_the_feed_goes_down(self, urlopen):
        routes = [{"id": 1, "name": "Blue", "color": "4472C4", "active": True, "stops": [10], "path": []}]
        stops = [{"id": 10, "name": "Science Hill", "lat": 41.316, "lon": -72.922}]
        urlopen.side_effect = [feed_answer(routes), feed_answer(stops)]
        self.assertEqual(self.client.get(reverse("network")).status_code, 200)

        # 30 seconds on, the cached answers have expired, and the feed is down.
        cache.clear()
        urlopen.side_effect = ConnectionRefusedError("refused")
        with self.assertLogs("transit.downtowner", "WARNING"), self.assertLogs("django.request", "ERROR"):
            self.assertEqual(self.client.get(reverse("network")).status_code, 503)
