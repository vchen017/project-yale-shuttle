import base64
import json
from io import StringIO
from unittest import mock

from cryptography.hazmat.primitives import serialization
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from py_vapid import Vapid
from pywebpush import WebPushException

from . import push
from .models import PushSubscription

KEYS = {"VAPID_PUBLIC_KEY": "test-public-key", "VAPID_PRIVATE_KEY": "test-private-key"}
# A device's keys as browsers send them: a 65-byte P-256 point and a 16-byte secret.
P256DH = base64.urlsafe_b64encode(b"\x04" + bytes(range(64))).rstrip(b"=").decode()
AUTH = base64.urlsafe_b64encode(bytes(range(16))).rstrip(b"=").decode()
CHROME_ON_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
SAFARI_ON_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)


def subscription_json(endpoint="https://fcm.googleapis.com/fcm/send/abc123"):
    return {"endpoint": endpoint, "expirationTime": None, "keys": {"p256dh": P256DH, "auth": AUTH}}


def make_user(name="rider@example.com"):
    return get_user_model().objects.create_user(name, password="a-test-password-1")


def make_device(user, endpoint="https://fcm.googleapis.com/fcm/send/abc123", agent=CHROME_ON_MAC):
    return PushSubscription.objects.create(
        user=user, endpoint=endpoint, p256dh=P256DH, auth=AUTH, user_agent=agent
    )


def push_error(status):
    return WebPushException("rejected", response=mock.Mock(status_code=status))


class PageTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_needs_a_log_in(self):
        response = Client().get(reverse("notifications"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('notifications')}")

    @override_settings(**KEYS)
    def test_lists_this_riders_devices_only_and_gives_the_page_the_public_key(self):
        make_device(self.user, agent=SAFARI_ON_IPHONE)
        make_device(make_user("other@example.com"), endpoint="https://fcm.googleapis.com/fcm/send/other")
        response = self.client.get(reverse("notifications"))
        self.assertContains(response, "Safari on iPhone")
        self.assertNotContains(response, "Chrome on Mac")
        self.assertContains(response, 'data-public-key="test-public-key"')
        self.assertContains(response, "Send a test notification")
        # The page's script compares the browser's subscription with these.
        self.assertContains(response, "fcm/send/abc123")
        self.assertNotContains(response, "fcm/send/other")

    @override_settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="")
    def test_without_keys_the_page_has_no_key_to_subscribe_with(self):
        response = self.client.get(reverse("notifications"))
        self.assertContains(response, 'data-public-key=""')
        self.assertContains(response, "None yet")

    def test_the_menu_links_to_it_and_every_page_offers_the_manifest(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, f'href="{reverse("notifications")}"')
        self.assertContains(response, f'rel="manifest" href="{reverse("manifest")}"')
        self.assertContains(response, 'rel="apple-touch-icon"')


class SubscribeTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def subscribe(self, body, client=None):
        return (client or self.client).post(
            reverse("push-subscribe"),
            json.dumps(body),
            content_type="application/json",
            headers={"user-agent": SAFARI_ON_IPHONE},
        )

    def test_saves_the_device_once_however_often_it_subscribes(self):
        self.assertEqual(self.subscribe(subscription_json()).status_code, 201)
        self.assertEqual(self.subscribe(subscription_json()).status_code, 201)
        device = PushSubscription.objects.get()
        self.assertEqual(device.user, self.user)
        self.assertEqual((device.p256dh, device.auth), (P256DH, AUTH))
        self.assertEqual(device.device, "Safari on iPhone")

    def test_a_shared_device_moves_to_whoever_subscribes_it_last(self):
        make_device(make_user("other@example.com"))
        self.subscribe(subscription_json())
        self.assertEqual(PushSubscription.objects.get().user, self.user)

    def test_turns_away_anything_that_isnt_a_subscription(self):
        too_long = "https://fcm.googleapis.com/" + "x" * 1000
        for body in [
            {},
            [],
            "text",
            {"endpoint": "https://fcm.googleapis.com/fcm/send/abc"},
            {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": "nope"},
            {"endpoint": 12, "keys": {"p256dh": P256DH, "auth": AUTH}},
            {"endpoint": "https://fcm.googleapis.com/x", "keys": {"p256dh": "", "auth": AUTH}},
            {"endpoint": "https://fcm.googleapis.com/x", "keys": {"p256dh": "BPk3", "auth": AUTH}},
            {"endpoint": "https://fcm.googleapis.com/x", "keys": {"p256dh": P256DH, "auth": "a1b2"}},
            {"endpoint": "https://fcm.googleapis.com/x", "keys": {"p256dh": P256DH, "auth": "not base64!"}},
            subscription_json(too_long),
        ]:
            with self.subTest(body=body), self.assertLogs("django.request", "WARNING"):
                self.assertEqual(self.subscribe(body).status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_only_accepts_known_push_services(self):
        for endpoint in [
            "https://evil.example.com/fcm.googleapis.com",
            "http://fcm.googleapis.com/fcm/send/abc",  # not https
            "https://googleapis.com.evil.example/x",
            "https://localhost/push",
        ]:
            with self.subTest(endpoint=endpoint), self.assertLogs("django.request", "WARNING"):
                self.assertEqual(self.subscribe(subscription_json(endpoint)).status_code, 400)
        for endpoint in [
            "https://fcm.googleapis.com/fcm/send/abc",
            "https://updates.push.services.mozilla.com/wpush/v2/abc",
            "https://web.push.apple.com/QJx",
            "https://wns2-par02p.notify.windows.com/w/?token=abc",
        ]:
            with self.subTest(endpoint=endpoint):
                self.assertTrue(push.is_push_service(endpoint))

    def test_needs_a_log_in_a_post_and_the_csrf_token(self):
        self.assertEqual(self.subscribe(subscription_json(), client=Client()).status_code, 302)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        with self.assertLogs("django.request", "WARNING"):  # Django logs each refusal
            self.assertEqual(self.client.get(reverse("push-subscribe")).status_code, 405)
            self.assertEqual(self.subscribe(subscription_json(), client=strict).status_code, 403)

    def test_unsubscribing_forgets_only_this_riders_device(self):
        mine = make_device(self.user)
        theirs = make_device(make_user("other@example.com"), endpoint="https://fcm.googleapis.com/x")
        for endpoint in (mine.endpoint, theirs.endpoint):
            response = self.client.post(
                reverse("push-unsubscribe"), json.dumps({"endpoint": endpoint}), "application/json"
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(list(PushSubscription.objects.all()), [theirs])

    def test_removing_a_device_from_the_list(self):
        mine = make_device(self.user)
        theirs = make_device(make_user("other@example.com"), endpoint="https://fcm.googleapis.com/x")
        with self.assertLogs("django.request", "WARNING"):
            self.assertEqual(self.client.post(reverse("push-remove", args=[theirs.pk])).status_code, 404)
        response = self.client.post(reverse("push-remove", args=[mine.pk]), follow=True)
        self.assertContains(response, "Removed Chrome on Mac")
        self.assertEqual(list(PushSubscription.objects.all()), [theirs])


@override_settings(**KEYS, VAPID_SUBJECT="https://yale-shuttle.example")
@mock.patch("notifications.push.webpush")
class SendTests(TestCase):
    def setUp(self):
        self.device = make_device(make_user())

    def test_sends_an_encrypted_signed_notification(self, webpush):
        self.assertTrue(push.send(self.device, "Leave now", "The Blue is 6 minutes away", url="/trips/1"))
        kwargs = webpush.call_args.kwargs
        self.assertEqual(kwargs["subscription_info"], self.device.info())
        self.assertEqual(
            json.loads(kwargs["data"]),
            {"title": "Leave now", "body": "The Blue is 6 minutes away", "url": "/trips/1"},
        )
        self.assertEqual(kwargs["vapid_private_key"], "test-private-key")
        self.assertEqual(kwargs["vapid_claims"], {"sub": "https://yale-shuttle.example"})
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_sent_at)

    def test_a_device_the_service_says_is_gone_is_deleted(self, webpush):
        for status in (404, 410):
            with self.subTest(status=status):
                device = make_device(self.device.user, endpoint=f"https://fcm.googleapis.com/{status}")
                webpush.side_effect = push_error(status)
                with self.assertLogs("notifications.push", "INFO"):
                    self.assertFalse(push.send(device, "Hi", "There"))
                self.assertFalse(PushSubscription.objects.filter(pk=device.pk).exists())

    def test_other_failures_raise_and_keep_the_device(self, webpush):
        for error in (push_error(500), push_error(None), TimeoutError("slow")):
            with self.subTest(error=error):
                webpush.side_effect = error
                with self.assertRaises(push.PushError):
                    push.send(self.device, "Hi", "There")
        self.assertTrue(PushSubscription.objects.filter(pk=self.device.pk).exists())

    def test_a_device_with_broken_keys_is_deleted_without_sending(self, webpush):
        PushSubscription.objects.filter(pk=self.device.pk).update(p256dh="x")
        self.device.refresh_from_db()
        with self.assertLogs("notifications.push", "WARNING"):
            self.assertFalse(push.send(self.device, "Hi", "There"))
        webpush.assert_not_called()
        self.assertFalse(PushSubscription.objects.exists())

    def test_a_bad_server_key_is_an_error_that_keeps_every_device(self, webpush):
        webpush.side_effect = ValueError("Could not deserialize key data")
        with self.assertRaisesMessage(push.PushError, "check VAPID_PRIVATE_KEY"):
            push.send(self.device, "Hi", "There")
        self.assertTrue(PushSubscription.objects.filter(pk=self.device.pk).exists())

    @override_settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="")
    def test_refuses_without_keys(self, webpush):
        with self.assertRaises(push.PushNotConfigured):
            push.send(self.device, "Hi", "There")
        webpush.assert_not_called()

    def test_a_test_notification_counts_what_happened(self, webpush):
        gone = make_device(self.device.user, endpoint="https://fcm.googleapis.com/gone")
        broken = make_device(self.device.user, endpoint="https://fcm.googleapis.com/broken")

        def answer(subscription_info, **kwargs):
            if subscription_info["endpoint"] == gone.endpoint:
                raise push_error(410)
            if subscription_info["endpoint"] == broken.endpoint:
                raise push_error(500)

        webpush.side_effect = answer
        with self.assertLogs("notifications.push", "INFO"):
            counts = push.send_test(PushSubscription.objects.all())
        self.assertEqual(counts, (1, 1, 1))
        level, text = push.describe(*counts)
        self.assertEqual(level, messages.WARNING)
        self.assertEqual(
            text,
            "Sent a test notification to 1 device. Removed 1 device that can't get "
            "notifications any more. 1 device failed; the server log has the details.",
        )

    def test_riders_can_test_their_own_devices(self, webpush):
        other = make_device(make_user("other@example.com"), endpoint="https://fcm.googleapis.com/x")
        self.client.force_login(self.device.user)
        response = self.client.post(reverse("push-test"), follow=True)
        self.assertContains(response, "Sent a test notification to 1 device.")
        sent_to = [call.kwargs["subscription_info"]["endpoint"] for call in webpush.call_args_list]
        self.assertEqual(sent_to, [self.device.endpoint])
        self.assertNotIn(other.endpoint, sent_to)

    def test_admins_can_test_any_device(self, webpush):
        admin = get_user_model().objects.create_superuser("admin@example.com", "", "a-test-password-1")
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin:notifications_pushsubscription_changelist"),
            {"action": "send_test_notification", "_selected_action": [self.device.pk]},
            follow=True,
        )
        self.assertContains(response, "Sent a test notification to 1 device.")
        webpush.assert_called_once()


class ServiceWorkerAndManifestTests(TestCase):
    def test_the_service_worker_is_served_from_the_root_and_never_cached(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertContains(response, "showNotification")
        self.assertContains(response, "notifications/icon-192.png")
        # A tapped notification takes the site's open window to its page, as an app would (#164).
        self.assertContains(response, "navigate(url)")

    def test_the_manifest_makes_the_site_installable(self):
        response = self.client.get(reverse("manifest"))
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        manifest = json.loads(response.content)
        self.assertEqual(manifest["display"], "standalone")
        # Opened from the Home Screen, it starts at the rider's next trip (#164), and
        # stays the app it was when it started at "/".
        self.assertEqual(manifest["start_url"], "/when-to-leave/")
        self.assertEqual(manifest["id"], "/")
        sizes = {(icon["sizes"], icon.get("purpose", "any")) for icon in manifest["icons"]}
        self.assertEqual(sizes, {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")})


class KeyAndDeviceTests(SimpleTestCase):
    def test_vapid_keys_prints_a_working_pair(self):
        out = StringIO()
        call_command("vapid_keys", stdout=out)
        lines = dict(line.split("=", 1) for line in out.getvalue().split())
        vapid = Vapid.from_string(lines["VAPID_PRIVATE_KEY"])
        public = vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        encoded = lines["VAPID_PUBLIC_KEY"]
        self.assertEqual(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)), public)

    def test_device_labels(self):
        for agent, label in [
            (SAFARI_ON_IPHONE, "Safari on iPhone"),
            (CHROME_ON_MAC, "Chrome on Mac"),
            ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0", "Firefox on Windows"),
            ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36", "Chrome on Android"),
            ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/131.0 Safari/537.36 Edg/131.0", "Edge on Windows"),
            ("", "A browser"),
        ]:
            with self.subTest(label=label):
                self.assertEqual(PushSubscription(user_agent=agent).device, label)
