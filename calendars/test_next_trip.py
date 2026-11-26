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
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.utils.html import escape

from crowding.services import RouteCrowding
from history.models import Calibration
from places.models import Place
from transit.downtowner import FeedUnavailable
from transit.next_buses import Board, Row

from . import next_trip
from .models import Calendar, Event
from .plans import EventPlan
from .test_leave import feed

User = get_user_model()


def shown_time(moment):
    """A time as the card shows it, "4:05 PM" Eastern: Django's own format,
    as the templates use it, since strftime's %-I is a glibc extension that
    Windows rejects (as in history's data_quality, #87)."""
    return date_format(timezone.localtime(moment), "g:i A")


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
            mock.patch("calendars.next_trip.crowding", side_effect=lambda routes, now=None: [self.crowd]),
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
        self.assertIn(f"Leave by {shown_time(leave_by)}", card)
        self.assertIn("3 min to spare", card)
        self.assertIn("Bus in 12–14 min", card)  # the feed's countdown to ours (#153)
        self.assertIn("at Union Station", card)
        self.assertIn('style="background-color: #00356b; color: #fff;"', card)  # its route's badge
        self.assertNotIn("color: ##", content)  # text_color has its # already (#149 had two)
        self.assertIn("data-live-bus", card)  # its map, where next-trip.js puts the bus
        self.assertIn('data-watching="true"', card)  # 30 minutes off: the page keeps refreshing it
        bus = json.loads(re.search(r'<script id="next-trip-bus" type="application/json">(.*?)</script>', content).group(1))
        self.assertEqual((bus["lat"], bus["lon"], bus["age_minutes"]), (41.3010, -72.9270, None))

    def test_the_card_s_event_folds_its_time_to_leave_but_keeps_the_agent(self):
        # #173: the calendar block isn't live, so the card's event doesn't repeat what the card says.
        later = Event.objects.create(
            calendar=self.calendar, uid="office-hours@yale", title="Office hours",
            start=timezone.now() + dt.timedelta(hours=3), location="Watson Center, 60 Sachem Street",
        )
        content = self.page()
        self.assertIn('<section id="next-trip"', content)
        lecture = content[content.index(f'id="event-{self.lecture.pk}"'):content.index(f'id="event-{later.pk}"')]
        self.assertIn('href="#next-trip"', lecture)
        self.assertIn("Your next trip: live on the card above", lecture)
        self.assertIn("MGT 656 lecture", lecture)
        self.assertIn("Ask the AI trip agent", lecture)  # the agent stays with its event (#148)
        for folded in ("cal-leave", "cal-rail", "data-trip-map"):
            self.assertNotIn(folded, lecture)
        office_hours = content[content.index(f'id="event-{later.pk}"'):]
        self.assertIn("Leave around", office_hours)  # an event the card doesn't show keeps its whole block
        self.assertIn("data-trip-map", office_hours)
        self.assertNotIn("live on the card above", office_hours)

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
        self.assertIn(f"Live bus times from {shown_time(live_from)}", card)
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
        self.assertIn("6 PM – midnight, every night", card)  # as the recorded polls show it (#173)
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


class FollowingTests(TestCase):
    """#163: once it's past "Leave by", the card follows the bus it told the
    rider to catch, from their stop to the event, refreshing in place."""

    def setUp(self):
        cache.clear()  # no corrections another test left cached (history.stored_corrections)
        self.start = timezone.now()
        self.user = User.objects.create_user("rider", password="not-the-real-password")
        self.client.force_login(self.user)
        Place.objects.create(user=self.user, name="Home", lat=41.2971, lon=-72.9261, is_home=True)
        calendar = Calendar.objects.create(user=self.user, ics_url="https://calendar.example.com/rider.ics")
        self.lecture = Event.objects.create(
            calendar=calendar, uid="lecture@yale", title="MGT 656 lecture",
            start=self.start + dt.timedelta(minutes=30), location="Watson Center, 60 Sachem Street",
        )
        self.at = {30: 12}  # stop -> the bus's minutes there: Union Station, as on the card before "Leave by"
        self.bus_id, self.last_stop, self.routes = 812, 97, []  # 97: the stop before Union Station
        crowd = RouteCrowding(1, "Blue - Weekday Daytime", "Blue", "00356b")
        for patch in (
            mock.patch("calendars.views.network", return_value=feed()),
            mock.patch("calendars.plans.next_buses", return_value=board()),
            mock.patch("transit.downtowner.etas", side_effect=self.etas),
            mock.patch("transit.downtowner.buses", side_effect=self.buses),
            mock.patch("transit.downtowner.routes", side_effect=lambda: self.routes),
            mock.patch("calendars.next_trip.crowding", side_effect=lambda routes, now=None: [crowd]),
            mock.patch("calendars.next_trip.detoured_routes", return_value=[]),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def etas(self, stop_id):
        minutes = self.at.get(stop_id)
        return [] if minutes is None else [{"route_id": 1, "bus_id": self.bus_id, "bus_name": "812", "minutes": minutes}]

    def buses(self):
        return [{"id": self.bus_id, "name": "812", "route": 1, "lat": 41.3010, "lon": -72.9270, "heading": 0,
                 "lastStop": self.last_stop, "lastUpdate": time.time() - 20}]

    def at_minute(self, later):
        return mock.patch("django.utils.timezone.now", return_value=self.start + dt.timedelta(minutes=later))

    def card(self, later=0):
        with self.at_minute(later):
            content = self.client.get(reverse("when-to-leave")).content.decode()
        return content[content.index("data-next-trip"):content.index('id="day-')]

    def refreshed(self, later):
        with self.at_minute(later):
            return self.client.get(reverse("next-trip")).json()

    def remembered(self):
        return self.client.session.get(next_trip.SESSION_KEY)

    def clock(self, later):
        return shown_time(self.start + dt.timedelta(minutes=later))

    def test_it_follows_the_bus_from_the_stop_to_the_event(self):
        # Before "Leave by" (in 11 minutes), the card remembers the bus it says to catch.
        before = self.card()
        key = re.search(r'data-key="([^"]+)"', before).group(1)
        self.assertIn(f"Leave by {self.clock(11)}", before)
        trip = self.remembered()
        self.assertEqual((trip["bus_id"], trip["board"]["name"], trip["building"]["name"]), (812, "Union Station", "Watson Center"))
        get_off, walk = trip["get_off"], trip["walk_from_stop"]
        self.routes = [{"id": 1, "stops": [97, 30, 99, get_off["id"]]}]  # 99 is the stop before the rider's

        # Past it: that bus, two minutes from Union Station.
        self.at = {30: 2, get_off["id"]: 9}
        card = self.card(later=12)
        self.assertIn(f'data-key="{key}"', card)  # the same trip: the page never reloads for it
        self.assertIn("On your way", card)
        self.assertIn("Bus in 2 min", card)
        self.assertIn("Walk to Union Station", card)
        self.assertIn(f"There by {self.clock(12 + 9 + walk)}", card)  # the bus at the stop, then the walk
        self.assertIn('class="journey"', card)
        self.assertIn('class="journey-leg is-here"', card)  # the bus before the first stop
        self.assertIn("data-live-bus", card)  # the trip's map, with the bus on it
        self.assertIn(reverse("next-trip-stop"), card)
        self.assertNotIn("Leave by", card)

        # The refreshes, as the bus moves on: the ride...
        self.last_stop, self.at = 30, {get_off["id"]: 6}
        riding = self.refreshed(later=15)
        self.assertEqual(riding["key"], key)
        self.assertTrue(riding["watching"])
        self.assertIn(f"Get off at {get_off['name']}", riding["html"])
        self.assertIn("in 6 min", riding["html"])
        self.assertIn("journey-leg is-ride is-here", riding["html"])
        self.assertEqual((riding["bus"]["lat"], riding["bus"]["lon"]), (41.3010, -72.9270))
        # ...the stop before theirs...
        self.last_stop, self.at = 99, {get_off["id"]: 1}
        self.assertIn("Get off at the next stop", self.refreshed(later=19)["html"])
        # ...theirs...
        self.at = {get_off["id"]: 0}
        self.assertIn("Get off here", self.refreshed(later=20)["html"])
        # ...and the walk the rest of the way.
        self.last_stop, self.at = get_off["id"], {}
        walking = self.refreshed(later=22)
        self.assertIn("Walk to Watson Center", walking["html"])
        self.assertEqual(walking["key"], key)
        # Once they should be there and the lecture has started, the card moves on: nothing's next.
        self.assertEqual(self.refreshed(later=max(22 + walk + 6, 31)), {"key": None})
        self.assertIsNone(self.remembered())

    def test_there_by_keeps_the_arrival_cushion(self):
        # #174: the nightly fit says buses with 5-10 minute countdowns reach a
        # stop up to 2.4 minutes later than the corrected time, so "There by"
        # counts 3 more minutes.
        Calibration.objects.create(
            route_id=None, bucket=1, delta_minutes=0.0, samples=40, cushion_minutes=0.0, arrival_cushion_minutes=2.4
        )
        self.card()
        trip = self.remembered()
        get_off, walk = trip["get_off"]["id"], trip["walk_from_stop"]
        self.at = {30: 2, get_off: 9}
        self.assertIn(f"There by {self.clock(12 + 9 + 3 + walk)}", self.card(later=12))

    def test_its_next_loop_never_reads_as_coming_again(self):
        self.card()
        get_off = self.remembered()["get_off"]["id"]
        self.at = {get_off: 5}
        self.refreshed(later=12)
        self.at = {get_off: 0}
        self.refreshed(later=17)
        self.at = {30: 15, get_off: 24}  # the same bus, coming round to Union Station again
        html = self.refreshed(later=19)["html"]
        self.assertIn("Walk to Watson Center", html)
        self.assertNotIn("Bus in", html)

    def test_the_card_remembers_a_new_bus_but_not_every_refresh(self):
        self.card()
        first = self.remembered()
        self.refreshed(later=1)  # the same bus, "Leave by" a minute on: not worth a session write
        self.assertEqual(self.remembered(), first)
        self.bus_id = 900  # the feed now has another bus at that countdown
        self.refreshed(later=2)
        self.assertEqual(self.remembered()["bus_id"], 900)

    def following(self):
        """The card, past "Leave by" (minute 12), following the bus."""
        self.card()
        self.at = {30: 2}
        card = self.card(later=12)
        self.assertIn("On your way", card)
        return card

    def test_a_cancelled_event_stops_being_followed(self):
        # Austin's review: the hourly refresh deletes an event cancelled after
        # "Leave by", and the card mustn't go on walking the rider there.
        self.following()
        self.lecture.delete()
        self.assertEqual(self.refreshed(later=13), {"key": None})  # nothing else is next
        self.assertIsNone(self.remembered())

    def test_a_moved_event_is_planned_again(self):
        key = re.search(r'data-key="([^"]+)"', self.following()).group(1)
        self.lecture.start = self.start + dt.timedelta(minutes=90)
        self.lecture.save(update_fields=["start"])
        moved = self.refreshed(later=13)
        self.assertNotEqual(moved["key"], key)  # another trip: the page reloads for it
        self.assertNotIn("On your way", moved["html"])
        self.assertIsNone(self.remembered())

    def test_an_event_moved_to_another_building_is_planned_again(self):
        self.following()
        self.lecture.location = "Sterling Memorial Library, 120 High Street"
        self.lecture.save(update_fields=["location"])
        self.assertNotIn("On your way", self.refreshed(later=13).get("html", ""))
        # The card plans the trip there instead, and remembers that one.
        self.assertNotEqual((self.remembered() or {}).get("building", {}).get("name"), "Watson Center")

    def test_a_renamed_event_is_still_followed_under_its_new_name(self):
        self.following()
        self.lecture.title = "MGT 656 review session"
        self.lecture.save(update_fields=["title"])
        html = self.refreshed(later=13)["html"]
        self.assertIn("On your way", html)
        self.assertIn("MGT 656 review session", html)

    def test_the_map_starts_from_home_as_it_is_now(self):
        self.card()
        self.at = {30: 2}
        Place.objects.filter(user=self.user, is_home=True).update(lat=41.3050, lon=-72.9300)
        card = self.card(later=12)
        data = json.loads(re.search(r'<script id="next-trip-map-data" type="application/json">(.*?)</script>', card).group(1))
        self.assertEqual(data["origin"], [41.3050, -72.9300])

    def test_opening_the_page_late_plans_from_home_as_before(self):
        # Nothing remembered: the rider never had the card open, so they're still at Home.
        card = self.card(later=12)
        self.assertNotIn("On your way", card)
        self.assertIn("Leave by", card)

    def test_stop_following_goes_back_to_the_next_trip(self):
        self.card()
        self.at = {30: 2}
        self.assertIn("On your way", self.card(later=12))
        with self.at_minute(12):
            response = self.client.post(reverse("next-trip-stop"))
        self.assertRedirects(response, reverse("when-to-leave"), fetch_redirect_response=False)
        self.assertNotIn("On your way", self.card(later=12))

    def test_with_the_feed_down_a_refresh_keeps_the_card(self):
        self.card()
        with mock.patch("transit.downtowner.etas", side_effect=FeedUnavailable("down")):
            self.assertEqual(self.refreshed(later=12), {"feed_down": True})
        self.assertIsNotNone(self.remembered())

    def test_a_bus_the_feed_stops_timing_says_so(self):
        self.card()
        self.at = {}
        html = self.refreshed(later=12)["html"]
        self.assertIn("We can't see your bus right now", html)
        self.assertIn("It may be running late", html)
