"""Phones' tab bar at the bottom of every page (#164). In its own file, so it
doesn't collide with other pull requests that add to core/tests.py."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from transit.downtowner import FeedUnavailable

TABS = [
    ("Next trip", "/when-to-leave/", "bi-clock-fill"),
    ("Buses", "/stops/", "bi-bus-front-fill"),
    ("Map", "/#stops", "bi-map-fill"),
    ("Me", "/me/", "bi-person-circle"),
]


def tab(label, href, icon, active):
    marked = 'class="tab-bar-item active" aria-current="page"' if active else 'class="tab-bar-item"'
    return f'<a {marked} href="{href}"><i class="bi {icon}" aria-hidden="true"></i>{label}</a>'


class TabBarTests(TestCase):
    def assertTabs(self, response, active):
        """The four tabs, in order, with only `active` marked as the page you're on."""
        self.assertContains(response, '<nav class="tab-bar d-md-none" aria-label="Main">')
        for label, href, icon in TABS:
            self.assertContains(response, tab(label, href, icon, label == active), html=True)
        page = response.content.decode()
        positions = [page.index(f">{label}</a>") for label, _, _ in TABS]
        self.assertEqual(positions, sorted(positions), "the tabs are out of order")

    def rider(self):
        self.client.force_login(get_user_model().objects.create_user("rider@example.com"))

    def test_phones_get_four_tabs_and_the_home_page_is_the_map(self):
        self.assertTabs(self.client.get("/"), active="Map")

    @mock.patch("calendars.views.network", side_effect=FeedUnavailable("down"))
    def test_next_trip_is_the_when_to_leave_page(self, network):
        self.rider()
        self.assertTabs(self.client.get(reverse("when-to-leave")), active="Next trip")

    def test_a_riders_own_pages_are_under_me(self):
        self.rider()
        for name in ("me", "my-places", "calendar", "notifications"):
            with self.subTest(name):
                self.assertTabs(self.client.get(reverse(name)), active="Me")

    def test_logging_in_is_under_me_too(self):
        self.assertTabs(self.client.get(reverse("login")), active="Me")

    def test_the_ab_test_page_has_no_tab_bar(self):
        """The graded run (#47) has to see the A/B page as it is."""
        response = self.client.get(reverse("abtest:page"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "tab-bar")

    def test_pages_are_drawn_edge_to_edge_and_open_full_screen_from_the_home_screen(self):
        response = self.client.get("/")
        self.assertContains(response, "viewport-fit=cover")
        self.assertContains(response, '<meta name="apple-mobile-web-app-capable" content="yes">', html=True)
        self.assertContains(response, '<meta name="mobile-web-app-capable" content="yes">', html=True)
