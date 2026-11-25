"""A rider's When to leave page in a real browser (#160): log in, see the
next trip and its map, ask the AI trip agent, and see its answer. Chromium,
driven by Playwright, against Django's live test server, so the page's own
JavaScript runs: the login form, the map (trip-map.js on MapLibre), the
next trip card (next-trip.js) and asking the agent in the background
(agent.js), which the other tests can only check as HTML.

Nothing reaches the outside world.
- On the server, the shuttle feed and the AI's model are mocked as in the
  other tests. The agent itself runs for real, in its own thread.
- In the browser, every request goes through route(): the site's own pass;
  Bootstrap, its icons and MapLibre come from the copies e2e/assets.py
  checked against the hashes the templates pin; the map's style is a
  stand-in with no tiles; anything else is refused, and the test says what.

Without Playwright, its browser or those copies, the test is skipped,
unless E2E=1 is set, as CI sets it: then it fails. To run it:

    python -m playwright install chromium-headless-shell
    python -m e2e.assets
    DJANGO_DEBUG=1 python manage.py test e2e

Where Playwright's own browser doesn't install (it no longer supports
macOS 13), E2E_CHANNEL=chrome drives an installed Google Chrome instead:
a separate headless one, with a throwaway profile.
"""

import datetime as dt
import json
import os
import time
import unittest
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.utils import timezone

from calendars.models import Calendar, Event
from calendars.test_leave import feed
from crowding.services import RouteCrowding
from places.models import Place
from transit.next_buses import Board, Row

from . import assets

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # not installed: requirements-dev.txt has it
    sync_playwright = None

User = get_user_model()
REQUIRED = os.environ.get("E2E") == "1"
PASSWORD = "only-for-the-browser-test"  # a test account's, in the test database
STYLE = {  # the map's base style: a plain background, no tiles to fetch
    "version": 8,
    "sources": {},
    "layers": [{"id": "land", "type": "background", "paint": {"background-color": "#eef1f5"}}],
}
# Chromium without a GPU draws WebGL (the map) in software only when asked to.
SOFTWARE_WEBGL = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
# What the page may ask the outside world for and be refused: the icon font, which only draws icons.
HARMLESS = ("https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/",)


@override_settings(OPENROUTER_API_KEY="test-openrouter-key")
class WhenToLeaveInABrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        cls._unsafe = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        cls.playwright = cls.browser = None
        try:
            if sync_playwright is None:
                raise RuntimeError("Playwright isn't installed: pip install -r requirements-dev.txt")
            if assets.missing():
                raise RuntimeError("The CDN files the test serves aren't fetched: python -m e2e.assets")
            # Playwright's sync API runs an event loop on this thread, which
            # Django takes for async code; the ORM calls here are synchronous.
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(channel=os.environ.get("E2E_CHANNEL") or None, args=SOFTWARE_WEBGL)
        except (RuntimeError, PlaywrightError) as problem:
            cls._stop()
            if REQUIRED:
                raise
            raise unittest.SkipTest(f"No browser test: {problem}") from problem
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._stop()

    @classmethod
    def _stop(cls):
        if cls.browser:
            cls.browser.close()
        if cls.playwright:
            cls.playwright.stop()
        if cls._unsafe is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = cls._unsafe

    def setUp(self):
        rider = User.objects.create_user("browser-rider", password=PASSWORD)
        Place.objects.create(user=rider, name="Home", lat=41.2971, lon=-72.9261, is_home=True)  # by Union Station
        calendar = Calendar.objects.create(user=rider, ics_url="https://calendar.example.com/browser.ics")
        Event.objects.create(
            calendar=calendar, uid="lecture@yale", title="MGT 656 lecture",
            start=timezone.now() + dt.timedelta(minutes=40), location="Watson Center, 60 Sachem Street",
        )
        board = Board(running=True, rows=[Row(
            route_id=1, name="Blue - Weekday Daytime", label="Blue", color="00356b",
            official=[12, 31], ours=14, cushions={12: 3},
        )])
        crowd = RouteCrowding(1, "Blue - Weekday Daytime", "Blue", "00356b")
        official = {"id": "a", "type": "function", "function": {"name": "get_official_arrival", "arguments": "{}"}}
        model = [  # the agent's model: it looks up the bus, then decides there's nothing to alert about yet
            {"role": "assistant", "content": None, "tool_calls": [official]},
            {"role": "assistant", "content": json.dumps({"notify": False, "notify_at": None, "message": ""})},
        ]
        for patch in (
            mock.patch("calendars.views.network", return_value=feed()),
            mock.patch("notifications.ask.network", return_value=feed()),
            mock.patch("calendars.plans.next_buses", return_value=board),
            mock.patch("transit.downtowner.etas", return_value=[
                {"route_id": 1, "bus_id": 812, "bus_name": "812", "minutes": 12},
            ]),
            mock.patch("transit.downtowner.buses", side_effect=lambda: [
                {"id": 812, "name": "812", "route": 1, "lat": 41.3010, "lon": -72.9270, "heading": 0,
                 "lastStop": 20, "lastUpdate": time.time() - 20},
            ]),
            mock.patch("calendars.next_trip.crowding", return_value=[crowd]),
            mock.patch("calendars.next_trip.detoured_routes", return_value=[]),
            mock.patch("assistant.agent.openrouter.chat_message", side_effect=model),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.refused = []
        self.context.route("**/*", self.route)

    def route(self, route):
        """The browser's every request: the site's own pass, the pinned CDN
        files come from their checked copies, the map's style is a stand-in,
        and the rest is refused."""
        url = route.request.url
        if url.startswith(self.live_server_url):
            return route.continue_()
        copy = assets.local(url)
        if copy:
            kind = "text/css" if url.endswith(".css") else "application/javascript"
            # The pages load these with crossorigin="anonymous", so the copy answers as the CDN does.
            return route.fulfill(path=copy, headers={"Content-Type": kind, "Access-Control-Allow-Origin": "*"})
        if url.startswith("https://tiles.openfreemap.org/styles/"):
            return route.fulfill(json=STYLE, headers={"Access-Control-Allow-Origin": "*"})
        self.refused.append(url)
        return route.abort()

    def test_a_rider_sees_the_next_trip_on_its_map_and_asks_the_agent(self):
        page = self.context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        # Log in, as a rider would, and land on When to leave.
        page.goto(f"{self.live_server_url}/login/?next=/when-to-leave/")
        page.get_by_label("Username").fill("browser-rider")
        page.get_by_label("Password").fill(PASSWORD)
        page.get_by_role("button", name="Log in").click()
        page.wait_for_url("**/when-to-leave/")

        # The next trip, with its map drawn by MapLibre: the trip's stops on it once it has loaded.
        card = page.locator("[data-next-trip]")
        expect(card).to_contain_text("MGT 656 lecture")
        expect(card).to_contain_text("Leave by")
        expect(card).to_contain_text("Bus in 12–14 min")
        expect(card.locator("[data-trip-map] canvas.maplibregl-canvas")).to_be_visible()
        expect(card.locator("[data-trip-map] .trip-stop").first).to_be_attached()

        # Ask the agent: the page asks in the background, then polls until it has answered.
        page.get_by_role("button", name="Ask the AI trip agent").click()
        answer = page.locator("[data-agent]").first
        expect(answer).to_contain_text("No alert right now", timeout=20_000)
        expect(answer.locator("[data-agent-poll]")).to_have_count(0)

        self.assertEqual(errors, [])  # no script failed on the page
        self.assertEqual([url for url in self.refused if not url.startswith(HARMLESS)], [])
