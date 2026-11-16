"""The home page's compact alerts list. In its own file, so it doesn't collide
with other pull requests that add to alerts/tests.py."""

import datetime as dt

from django.template.loader import render_to_string
from django.test import SimpleTestCase


def alert(n):
    return {
        "source": "downtowner",
        "title": f"Alert {n}",
        "message": f"Detour number {n}, with the long details riders need.",
        "link_url": "",
        "link_text": "",
        "posted_at": dt.datetime(2026, 11, 16, 18, n, tzinfo=dt.UTC),
    }


class CompactAlertsTests(SimpleTestCase):
    def render(self, count):
        return render_to_string("alerts/_list.html", {"alerts": [alert(n) for n in range(count)], "feed_ok": True})

    def test_two_alerts_show_without_a_more_button(self):
        html = self.render(2)
        self.assertEqual(html.count('class="service-alert"'), 2)
        self.assertNotIn("more alert", html)

    def test_the_rest_wait_behind_show_more(self):
        html = self.render(5)
        self.assertEqual(html.count('class="service-alert"'), 5)
        self.assertIn("Show 3 more alerts", html)
        # The first two come before the button, the other three after it.
        self.assertLess(html.index("Alert 1"), html.index("Show 3 more"))
        self.assertLess(html.index("Show 3 more"), html.index("Alert 2"))

    def test_one_extra_alert_is_singular(self):
        self.assertIn("Show 1 more alert<", self.render(3))

    def test_each_alert_keeps_its_full_text_for_when_its_opened(self):
        html = self.render(1)
        self.assertIn("<details>", html)
        self.assertIn("Detour number 0, with the long details riders need.", html)
