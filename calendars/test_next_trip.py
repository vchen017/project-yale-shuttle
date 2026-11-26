"""The next trip card on the when-to-leave page (#157), and the endpoint that
keeps it live. The feed is mocked at calendars.views.network, the live board
at calendars.plans.next_buses, and the feed's ETAs and buses at
transit.downtowner, so no test reaches the network."""

import datetime as dt
import json
import re
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from crowding.models import CrowdReport
from crowding.services import RouteCrowding, crowding
from places.models import Place
from transit.downtowner import FeedUnavailable
from transit.next_buses import Board, Row

from . import next_trip
from .models import Calendar, Event
from .plans import EventPlan
from .test_leave import feed

User = get_user_model()


def board(ours=14, official=(12, 31), cushions=None):
    """The live board: the Blue, ours `ours` minutes out where the feed says
    official[0], and the cushion each countdown needs (#155)."""
    return Board(running=True, rows=[Row(
        route_id=1, name="Blue - Weekday Daytime", label="Blue", color="00356b",
        official=list(official), ours=ours, cushions={12: 3} if cushions is None else cushions,
    )])


def arrivals(minutes=12, bus_id=812):
    """The feed's ETAs at the boarding stop: that bus, `minutes` out."""
    return [{"route_id": 1, "bus_id": bus_id, "bus_name": str(bus_id), "minutes": minutes}]


def buses(seconds_ago=30, lat=41.3010, lon=-72.9270, bus_id=812):
    return [{"id": bus_id, "name": str(bus_id), "route": 1, "lat": lat, "lon": lon, "heading": 0,
             "lastStop": 10, "lastUpdate": time.time() - seconds_ago}]


class NextTripTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rider", password="not-the-real-password")
        self.client.force_login(self.user)
        # By Union Station, as in the page's tests: the stop is at Home's door.
        Place.objects.create(user=self.user, name="Home", lat=41.2971, lon=-72.9261, is_home=True)
        self.calendar = Calendar.objects.create(user=self.user, ics_url="https://calendar.example.com/rider.ics")
        self.lecture = Event.objects.create(
            calendar=self.calendar, uid="lecture@yale", title="MGT 656 lecture",
            start=timezone.now() + dt.timedelta(minutes=30), location="Watson Center, 60 Sachem Street",
        )
        self.crowd = RouteCrowding(1, "Blue - Weekday Daytime", "Blue", "00356b")
        self.detours = []
        self.etas, self.buses = arrivals(), buses()

    def mocks(self, live_board):
        return (
            mock.patch("calendars.views.network", return_value=feed()),
            mock.patch("calendars.plans.next_buses", return_value=live_board),
            mock.patch("transit.downtowner.etas", side_effect=lambda stop_id: self.etas),
            mock.patch("transit.downtowner.buses", side_effect=lambda: self.buses),
            mock.patch(  # self.crowd stands in for the reports; None reads the real ones
                "calendars.next_trip.crowding",
                side_effect=lambda routes, now=None: [self.crowd] if self.crowd is not None else crowding(routes, now),
            ),
            mock.patch("calendars.next_trip.detoured_routes", side_effect=lambda now=None: self.detours),
        )

    def page(self, live_board=None):
        patches = self.mocks(live_board or board())
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            return self.client.get(reverse("when-to-leave")).content.decode()

    def refreshed(self, live_board=None):
        patches = self.mocks(live_board or board())
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = self.client.get(reverse("next-trip"))
        self.assertEqual(response["Content-Type"], "application/json")
        return response.json()

    def test_the_next_trip_leads_with_when_to_leave_and_the_bus_as_a_range(self):
        content = self.page()
        card = content[content.index("data-next-trip"):content.index('id="day-')]
        leave_by = timezone.localtime(timezone.now() + dt.timedelta(minutes=11))  # bus in 14, 3 to spare
        self.assertIn("MGT 656 lecture", card)
        self.assertIn(f"Leave by {leave_by:%-I:%M %p}", card)
        self.assertIn("3 min to spare", card)
        self.assertIn("Bus in 12–14 min", card)  # the feed's countdown to ours (#153)
        self.assertIn("at Union Station", card)
        self.assertIn('style="background-color: #00356b; color: #fff;"', card)  # its route's badge
        self.assertNotIn("color: ##", content)  # text_color has its # already (#149 had two)
        self.assertIn("data-live-bus", card)  # its map, where next-trip.js puts the bus
        self.assertIn('data-watching="true"', card)  # 30 minutes off: the page keeps refreshing it
        bus = json.loads(re.search(r'<script id="next-trip-bus" type="application/json">(.*?)</script>', content).group(1))
        self.assertEqual((bus["lat"], bus["lon"], bus["age_minutes"]), (41.3010, -72.9270, None))

    def test_crowding_and_a_detour_show_as_chips(self):
        self.crowd.current_level = 3
        self.detours = [{"route_id": 1, "route_name": "Blue", "bus_count": 1, "since": timezone.now()}]
        card = self.page()
        self.assertIn('class="agent-meter crowd-3"', card)
        self.assertIn(escape(self.crowd.current_label), card)  # "Full (can't board)"
        self.assertIn("Possible detour", card)

    def test_the_usual_crowding_says_so(self):
        self.crowd.typical_now = mock.Mock(label="Busy", level=2)
        self.assertIn("Busy, usually", self.page())

    def test_demo_samples_never_show_as_the_usual_crowding(self):
        """The seed's samples fill in the crowding page's pattern for graders,
        but the card reads real riders' reports alone (#181)."""
        now = timezone.now()
        self.crowd = None

        def report(level, is_sample):
            for days_ago in (1, 2, 3):  # this hour on the Eastern clock, the last three days
                CrowdReport.objects.create(
                    user=self.user, route_id=1, route_name="Blue - Weekday Daytime", level=level,
                    created_at=timezone.localtime(now) - dt.timedelta(days=days_ago), is_sample=is_sample,
                )

        def card():
            content = self.page()
            return content[content.index("data-next-trip"):content.index('id="day-')]

        with mock.patch("django.utils.timezone.now", return_value=now):  # one hour all through
            report(CrowdReport.Level.FULL, is_sample=True)
            self.assertNotIn("agent-meter crowd-", card())
            report(CrowdReport.Level.SEATS, is_sample=False)
            self.assertIn("Seats available, usually", card())

    def test_the_card_refreshes_without_reloading_the_page(self):
        # #157's live updates: the endpoint the page asks every REFRESH_SECONDS.
        content = self.page()
        key = re.search(r'data-key="([^"]+)"', content).group(1)
        first = self.refreshed()
        self.assertEqual(first["key"], key)
        self.assertIn("Bus in 12–14 min", first["html"])
        self.assertTrue(first["watching"])
        # Five minutes on, by the feed: the bus is closer, and it moved.
        self.etas, self.buses = arrivals(minutes=7), buses(lat=41.3050, lon=-72.9250)
        later = self.refreshed(board(ours=8, official=(7, 26), cushions={7: 1}))
        self.assertEqual(later["key"], key)  # the same trip: no reload, the card swaps
        self.assertIn("Bus in 7–8 min", later["html"])
        self.assertIn("1 min to spare", later["html"])
        self.assertEqual((later["bus"]["lat"], later["bus"]["lon"]), (41.3050, -72.9250))

    def test_the_refresh_names_the_next_event_and_none_once_nothing_is_left(self):
        self.lecture.start = timezone.now() - dt.timedelta(minutes=1)  # it has started
        self.lecture.save(update_fields=["start"])
        self.assertEqual(self.refreshed(), {"key": None})
        response = self.client.get(reverse("next-trip"))
        self.assertIn("no-cache", response["Cache-Control"])  # never a stale card from a cache

    def test_a_stale_bus_says_how_old_and_a_frozen_one_leaves_the_map(self):
        self.buses = buses(seconds_ago=5 * 60)
        self.assertIn("Bus last seen 5 min ago", self.page())
        self.buses = buses(seconds_ago=15 * 60)
        self.assertIsNone(self.refreshed()["bus"])

    def test_an_event_too_far_off_says_when_live_times_start(self):
        self.lecture.start = timezone.now() + dt.timedelta(hours=3)
        self.lecture.save(update_fields=["start"])
        card = self.page()
        live_from = timezone.localtime(self.lecture.start - dt.timedelta(minutes=45))
        self.assertIn("Leave around", card)
        self.assertIn(f"Live bus times from {live_from:%-I:%M %p}", card)
        self.assertNotIn("Bus in", card)
        # Nothing live to refresh yet: the page asks once, when the live window opens.
        self.assertIn('data-watching="false"', card)
        self.assertIn(f'data-live-from="{live_from.isoformat()}"', card)
        self.assertFalse(self.refreshed()["watching"])

    def test_when_no_shuttle_runs_it_offers_yales_other_ways_with_tap_to_call(self):
        def no_ride(event, home, **kwargs):
            building = {"name": "Watson Center", "lat": 41.3156, "lon": -72.9238}
            return EventPlan(event=event, building=building, plan=None, walk_all=26)

        with mock.patch("calendars.views.plans.plan_event", side_effect=no_ride):
            card = self.page()
        self.assertIn("No shuttle runs then", card)
        self.assertIn("Walking takes 26 min", card)
        self.assertIn('href="tel:+12032815470,1"', card)  # Yale Transit dispatch, ext. 1
        self.assertIn('href="tel:+12034329255"', card)  # 2-Walk
        self.assertIn("Night shuttle to your door", card)
        self.assertNotIn("data-live-bus", card)  # nothing to follow on a map
        self.assertIn('data-watching="false"', card)  # nor to keep refreshing

    def test_with_the_feed_down_there_is_no_card(self):
        with mock.patch("calendars.views.network", side_effect=FeedUnavailable("down")), \
             mock.patch("calendars.plans.next_buses", side_effect=FeedUnavailable("down")):
            content = self.client.get(reverse("when-to-leave")).content.decode()
        self.assertNotIn("data-next-trip", content)  # "no shuttle" couldn't be told from "the feed didn't say"
        # A refresh while it's down keeps the card the page has, rather than reloading it away.
        with mock.patch("calendars.views.network", side_effect=FeedUnavailable("down")):
            self.assertEqual(self.client.get(reverse("next-trip")).json(), {"feed_down": True})

    def test_the_endpoint_is_the_riders_own(self):
        self.client.logout()
        response = self.client.get(reverse("next-trip"))
        self.assertEqual(response.status_code, 302)  # to the log-in page


class NextPlanTests(TestCase):
    def test_the_first_event_with_a_building_and_a_way_there(self):
        now = timezone.now()
        later = Event(uid="b", title="B", start=now + dt.timedelta(hours=1), location="x")
        unknown = EventPlan(event=Event(uid="a", title="A", start=now + dt.timedelta(minutes=5), location="?"),
                            building=None)
        no_home = EventPlan(event=Event(uid="c", title="C", start=now + dt.timedelta(minutes=10), location="x"),
                            building={"name": "Watson Center"})
        rideable = EventPlan(event=later, building={"name": "Watson Center"}, walk_all=26)
        self.assertIs(next_trip.next_plan([unknown, no_home, rideable], now), rideable)
        self.assertIsNone(next_trip.next_plan([unknown, no_home], now))
