"""Event plans (#26): matching a location to a building, and the route, stop
and time to leave between a Home and that building. The stops and routes are
stubs borrowed from transit's trip tests, and the live board is mocked, so
nothing here touches the network. In its own file, so it doesn't collide with
other pull requests."""

import datetime as dt
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from transit.downtowner import FeedUnavailable
from transit.next_buses import Board, Row
from transit.test_trips import PAYNE_WHITNEY, SCIENCE_HILL, SERVED, UNION

from .models import Event
from .plans import EventPlan, catchable_minutes, match_building
from .plans import plan_event as plan_one

ET = ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=ET)
START = NOW + dt.timedelta(days=3)
HOME = SimpleNamespace(lat=41.2971, lon=-72.9261)  # by Union Station, far enough that the shuttle beats walking
ROUTES = {1: {"id": 1, "name": "Blue - Weekday Daytime"}, 2: {"id": 2, "name": "Orange - Weekday Daytime"}}


def event(location="Watson Center, 60 Sachem Street", start=START):
    return Event(uid="e@yale", title="Lecture", start=start, location=location)


def row(ours=8, official=(9,)):
    """A live board row: the Blue, ours 8 minutes out, the feed saying 9."""
    return Row(route_id=1, name="Blue - Weekday Daytime", label="Blue", color="00356b", official=list(official), ours=ours)


def plan_event(event, home=HOME, now=NOW, stops=SERVED, routes=ROUTES, **more):
    return plan_one(event, home, now=now, stops=stops, routes_by_id=routes, **more)


class MatchBuildingTests(SimpleTestCase):
    def test_a_location_with_its_address_matches_on_the_building(self):
        self.assertEqual(match_building("Watson Center, 60 Sachem Street")["name"], "Watson Center")

    def test_the_room_piece_that_matches_nothing_is_skipped(self):
        self.assertEqual(match_building("Room B-07; SSS")["name"], "Sheffield-Sterling-Strathcona Hall")

    def test_a_bare_building_name_matches(self):
        self.assertEqual(match_building("Watson Center")["name"], "Watson Center")

    def test_a_street_address_alone_is_no_guess(self):
        self.assertIsNone(match_building("60 Sachem Street"))

    def test_a_city_or_state_piece_never_names_a_building(self):
        # The pieces of a Google-style address are tried on their own, so
        # "New Haven" used to match Yale New Haven Hospital — a guess.
        self.assertIsNone(match_building("Shake Shack, 986 Chapel St, New Haven, CT 06510, USA"))
        self.assertIsNone(match_building("165 Whitney Ave, New Haven, CT 06511, USA"))

    def test_a_place_that_isnt_one_of_our_buildings_is_none(self):
        self.assertIsNone(match_building("New Haven Green"))
        self.assertIsNone(match_building(""))
        self.assertIsNone(match_building(None))


class PlanEventTests(SimpleTestCase):
    def test_the_far_future_plans_on_the_shared_route_and_estimates_the_wait(self):
        with mock.patch("calendars.plans.next_buses") as board:
            plan = plan_event(event())
        board.assert_not_called()  # nothing live to predict from three days out
        self.assertEqual((plan.plan.board_at["name"], plan.plan.get_off_at["name"]), ("Union Station", "Science Hill"))
        self.assertEqual(plan.route_names, ["Blue - Weekday Daytime"])
        self.assertTrue(plan.estimate)
        self.assertIsNone(plan.row)
        # start minus the walk from the stop (2), minus the typical wait, minus the walk to the stop (0).
        self.assertEqual(plan.leave_by, START - dt.timedelta(minutes=2 + 15))

    def test_an_event_soon_uses_the_live_board_for_when_to_leave(self):
        soon = NOW + dt.timedelta(minutes=20)
        with mock.patch("calendars.plans.next_buses", return_value=Board(running=True, rows=[row()])) as board:
            plan = plan_event(event(start=soon))
        board.assert_called_once_with(UNION["id"], SCIENCE_HILL["id"])
        self.assertEqual(plan.row.ours, 8)
        self.assertFalse(plan.estimate)
        self.assertEqual(plan.leave_by, NOW + dt.timedelta(minutes=8))  # bus in 8; the stop is at Home's door

    def test_an_event_soon_takes_the_feed_s_countdown_when_we_have_no_prediction(self):
        soon = NOW + dt.timedelta(minutes=20)
        with mock.patch("calendars.plans.next_buses", return_value=Board(running=True, rows=[row(ours=None, official=(9,))])):
            plan = plan_event(event(start=soon))
        self.assertEqual(plan.leave_by, NOW + dt.timedelta(minutes=9))

    def test_an_event_soon_with_no_usable_bus_falls_back_to_the_estimate(self):
        soon = NOW + dt.timedelta(minutes=20)
        with mock.patch("calendars.plans.next_buses", return_value=Board(running=True, rows=[])):
            plan = plan_event(event(start=soon))
        self.assertTrue(plan.estimate)
        self.assertIsNone(plan.row)

    def test_a_bus_the_walk_cant_reach_is_skipped_for_a_later_one(self):
        # Payne Whitney is an 8-minute walk from this home: a bus 3 minutes out
        # is gone before we arrive, so the plan takes the one 12 minutes out.
        away = SimpleNamespace(lat=41.3072, lon=-72.9298)
        soon = NOW + dt.timedelta(minutes=20)
        rows = [row(ours=3, official=(3, 12))]
        with mock.patch("calendars.plans.next_buses", return_value=Board(running=True, rows=rows)) as board:
            plan = plan_event(event(start=soon), home=away)
        board.assert_called_once_with(PAYNE_WHITNEY["id"], SCIENCE_HILL["id"])
        self.assertIs(plan.row, rows[0])
        self.assertEqual(plan.bus_minutes, 12)  # the later bus, not the row's soonest
        self.assertFalse(plan.estimate)
        self.assertEqual(plan.leave_by, NOW + dt.timedelta(minutes=12 - 8))

    def test_when_no_bus_allows_the_walk_it_falls_back_to_the_estimate(self):
        away = SimpleNamespace(lat=41.3072, lon=-72.9298)
        soon = NOW + dt.timedelta(minutes=20)
        with mock.patch("calendars.plans.next_buses", return_value=Board(running=True, rows=[row(ours=3, official=(3, 5))])):
            plan = plan_event(event(start=soon), home=away)
        self.assertTrue(plan.estimate)
        self.assertIsNone(plan.row)
        self.assertEqual(plan.leave_by, soon - dt.timedelta(minutes=2 + 15 + 8))

    def test_a_down_feed_becomes_an_estimate_instead_of_an_error(self):
        soon = NOW + dt.timedelta(minutes=20)
        with mock.patch("calendars.plans.next_buses", side_effect=FeedUnavailable("down")):
            plan = plan_event(event(start=soon))
        self.assertTrue(plan.estimate)

    def test_walking_is_quicker_skips_the_buses(self):
        nearby = SimpleNamespace(lat=41.3156, lon=-72.9238)  # Watson Center's own corner
        with mock.patch("calendars.plans.next_buses") as board:
            plan = plan_event(event(location="Watson Center"), home=nearby)
        board.assert_not_called()
        self.assertTrue(plan.walking_is_quicker)
        self.assertFalse(plan.estimate)
        self.assertEqual(plan.leave_by, START - dt.timedelta(minutes=plan.plan.walk_all_the_way))

    def test_a_location_we_cant_match_is_location_unknown(self):
        plan = plan_event(event(location="New Haven Green"))
        self.assertTrue(plan.location_unknown)
        self.assertIsNone(plan.plan)
        self.assertIsNone(plan.leave_by)
        self.assertEqual(plan, EventPlan(event=plan.event, building=None))

    def test_no_home_yet_still_matches_the_building(self):
        plan = plan_event(event(), home=None)
        self.assertFalse(plan.location_unknown)
        self.assertIsNone(plan.plan)
        self.assertIsNone(plan.leave_by)

    def test_no_route_linking_the_two_places_gives_no_ride(self):
        # Only Payne Whitney is near the start, and the Orange doesn't reach Union Station.
        plan = plan_event(event(), home=SimpleNamespace(lat=41.3130, lon=-72.9298), stops=[PAYNE_WHITNEY, UNION])
        self.assertFalse(plan.location_unknown)
        self.assertIsNone(plan.plan)
        self.assertEqual(plan.route_names, [])
        self.assertEqual(plan.walk_all, 7)  # the walk all the way is still worth saying

    def test_the_feed_being_down_still_names_the_building(self):
        plan = plan_event(event(), stops=None, routes=None)
        self.assertFalse(plan.location_unknown)
        self.assertIsNone(plan.plan)
        self.assertIsNone(plan.leave_by)
        self.assertEqual(plan.walk_all, 26)  # the walk is computable without the feed


class CatchableMinutesTests(SimpleTestCase):
    """The bus the page plans around, chosen without a board row, as the trip
    watch chooses it for the AI (#153)."""

    def test_our_prediction_for_the_soonest_bus_when_the_walk_allows_it(self):
        self.assertEqual(catchable_minutes(8, [7, 20], walk=5), 8)

    def test_otherwise_the_first_later_bus_the_walk_allows(self):
        self.assertEqual(catchable_minutes(3, [3, 4, 15], walk=8), 15)
        self.assertIsNone(catchable_minutes(3, [3], walk=8))

    def test_the_feeds_countdown_without_our_prediction_and_ours_without_a_bus_in_the_feed(self):
        self.assertEqual(catchable_minutes(None, [9], walk=5), 9)
        self.assertEqual(catchable_minutes(12, [], walk=5), 12)
        self.assertIsNone(catchable_minutes(None, [], walk=5))


class AtTheEventsHourTests(SimpleTestCase):
    """An estimate plans on the routes running at the event's weekday and
    hour, not the ones running now (Vivian, #126 review): the 9 PM lecture
    checked in the afternoon rides the night route. The history lookup itself
    needs the database, so here it's a stub."""

    def test_an_estimate_plans_on_the_routes_running_at_the_event(self):
        # Only the night Orange runs at 9 PM, and it doesn't reach Union
        # Station, so the plan boards at Payne Whitney — 22 minutes' walk from
        # this home, not the daytime Blue at the door.
        plan = plan_event(event(), running_at=lambda start, now: {2})
        self.assertEqual(plan.route_names, ["Orange - Weekday Daytime"])
        self.assertEqual(plan.plan.board_at["name"], "Payne Whitney")
        self.assertEqual(plan.plan.get_off_at["name"], "Science Hill")
        self.assertTrue(plan.estimate)
        # start minus the walk from the stop (2), minus the wait, minus the
        # walk to the stop (22, Payne Whitney).
        self.assertEqual(plan.leave_by, START - dt.timedelta(minutes=22 + 15 + 2))

    def test_when_nothing_runs_at_the_event_hour_there_is_no_ride(self):
        # 3 AM: the history says nothing runs, so there are no stops to board
        # at, and the walk all the way is what's left.
        plan = plan_event(event(), running_at=lambda start, now: set())
        self.assertIsNone(plan.plan)
        self.assertIsNone(plan.leave_by)
        self.assertEqual(plan.walk_all, 26)

    def test_without_history_the_estimate_falls_back_to_the_routes_running_now(self):
        plan = plan_event(event(), running_at=lambda start, now: None)
        self.assertEqual(plan.plan.board_at["name"], "Union Station")
        self.assertEqual(plan.route_names, ["Blue - Weekday Daytime"])
        self.assertTrue(plan.estimate)

    def test_an_event_soon_never_asks_the_history(self):
        def fails(start, now):
            raise AssertionError("inside the live window, now is the event's hour")

        soon = NOW + dt.timedelta(minutes=20)
        with mock.patch("calendars.plans.next_buses", return_value=Board(running=True, rows=[row()])):
            plan = plan_event(event(start=soon), running_at=fails)
        self.assertFalse(plan.estimate)
        self.assertEqual(plan.leave_by, NOW + dt.timedelta(minutes=8))
