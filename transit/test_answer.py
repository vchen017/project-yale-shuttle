"""The trip page's one answer, ride time and map (#131). The Downtowner feed
is mocked, at transit.answer.downtowner.etas for the unit tests and at
transit.downtowner.urlopen for the page's, so no test reaches the network. In
its own file, so it doesn't collide with other pull requests."""

import datetime as dt
import json
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import answer as answers
from .next_buses import Board, Row
from .test_next_buses import ROUTES, feed
from .test_trips import GROVE, UNION
from .trips import Plan

# 5:00 PM UTC is noon in New Haven (EST), so clock times are easy to read.
NOW = dt.datetime(2026, 11, 19, 17, 0, tzinfo=dt.timezone.utc)
BLUE = Row(route_id=1, name="Blue - Weekday Daytime", label="Blue", color="4472C4")
BOARD = Board(running=True, rows=[BLUE])


def eta(minutes, bus_id=812, route_id=1):
    return {"route_id": route_id, "bus_id": bus_id, "bus_name": str(bus_id), "minutes": minutes}


def plan(walk_to=6, walk_from=3, walk_all=40):
    return Plan(board_at=GROVE, get_off_at=UNION, walk_to_stop=walk_to, walk_from_stop=walk_from, walk_all_the_way=walk_all)


def etas_by_stop(at_grove, at_union):
    """A stand-in for downtowner.etas that answers per stop."""
    return mock.patch(
        "transit.answer.downtowner.etas",
        side_effect=lambda stop_id: {GROVE["id"]: at_grove, UNION["id"]: at_union}[stop_id],
    )


def ride(at_grove, at_union, ours=None, **plan_kwargs):
    """The answer for a trip whose board shows the feed's countdowns at Grove
    (and our prediction, when `ours` is given)."""
    official = sorted(e["minutes"] for e in at_grove if e["route_id"] == 1)
    board = Board(running=True, rows=[Row(1, BLUE.name, BLUE.label, BLUE.color, official=official, ours=ours)])
    with etas_by_stop(at_grove, at_union):
        return answers.answer(plan(**plan_kwargs), board, 40, NOW)


class AnswerTests(SimpleTestCase):
    def test_it_leads_with_when_to_leave_for_which_bus_and_how_early_they_will_be(self):
        result = ride([eta(10)], [eta(22)], walk_to=6)
        self.assertEqual(result.kind, answers.RIDE)
        self.assertEqual(
            result.headline, "Leave in 2 min for the 12:10 PM Blue - Weekday Daytime, to be at the stop 2 min early"
        )

    def test_the_buffer_is_taken_off_the_time_to_leave(self):
        result = ride([eta(20)], [eta(32)], walk_to=6)
        self.assertEqual((result.leave_in, result.early_minutes), (20 - 6 - answers.BUFFER_MINUTES, answers.BUFFER_MINUTES))

    def test_a_bus_too_close_for_the_whole_buffer_means_leave_now_and_a_smaller_margin(self):
        result = ride([eta(7)], [eta(19)], walk_to=6)
        self.assertEqual((result.leave_in, result.early_minutes), (0, 1))
        self.assertEqual(
            result.headline, "Leave now for the 12:07 PM Blue - Weekday Daytime, to be at the stop 1 min early"
        )

    def test_a_bus_that_arrives_as_they_do_is_still_offered_with_no_margin(self):
        result = ride([eta(6)], [eta(18)], walk_to=6)
        self.assertEqual((result.leave_in, result.early_minutes), (0, 0))
        self.assertEqual(
            result.headline, "Leave now for the 12:06 PM Blue - Weekday Daytime, to be at the stop just as it arrives"
        )

    def test_the_ride_is_the_same_buss_gap_between_the_two_stops(self):
        result = ride([eta(10)], [eta(22)], walk_to=6, walk_from=3)
        self.assertEqual(result.ride_minutes, 12)
        self.assertEqual(result.arrive_at, NOW + dt.timedelta(minutes=10 + 12 + 3))
        # Set out, spend the margin at the stop, ride, walk on.
        self.assertEqual(result.total_minutes, 6 + 2 + 12 + 3)

    def test_another_bus_at_the_far_stop_is_not_this_buss_ride(self):
        result = ride([eta(10, bus_id=812)], [eta(14, bus_id=900), eta(25, bus_id=812)])
        self.assertEqual(result.ride_minutes, 15)

    def test_a_countdown_at_the_far_stop_from_before_the_bus_boards_is_ignored(self):
        # The same bus 4 minutes from the far stop, 10 from ours: it passes the
        # far stop first (it's coming round the loop), so that's not the ride.
        result = ride([eta(10)], [eta(4), eta(30)])
        self.assertEqual(result.ride_minutes, 20)

    def test_a_bus_the_far_stop_doesnt_show_gives_no_ride_time_and_no_guess(self):
        result = ride([eta(10)], [eta(22, bus_id=999)])
        self.assertEqual(result.kind, answers.RIDE)
        self.assertIsNone(result.ride_minutes)
        self.assertIsNone(result.arrive_at)
        self.assertIsNone(result.total_minutes)

    def test_a_bus_without_an_id_gives_no_ride_time(self):
        self.assertIsNone(ride([eta(10, bus_id=None)], [eta(22)]).ride_minutes)

    def test_it_skips_a_bus_the_rider_cant_reach_in_time(self):
        # An 8-minute walk: the bus in 3 minutes leaves before they're there.
        result = ride([eta(3, bus_id=1), eta(12, bus_id=2)], [eta(20, bus_id=2)], walk_to=8)
        self.assertEqual(result.bus_minutes, 12)
        self.assertEqual(result.leave_in, 2)
        self.assertEqual(result.ride_minutes, 8)  # the second bus's own ride

    def test_at_the_stop_there_is_nothing_to_leave_for(self):
        result = ride([eta(6)], [eta(18)], walk_to=0)
        self.assertEqual(result.headline, "The 12:06 PM Blue - Weekday Daytime reaches your stop in 6 min")

    def test_only_buses_on_a_route_that_links_both_stops_count(self):
        with etas_by_stop([eta(7, route_id=2), eta(15, route_id=1)], [eta(27, route_id=1)]):
            board = Board(running=True, rows=[Row(1, BLUE.name, BLUE.label, BLUE.color, official=[15])])
            result = answers.answer(plan(), board, 40, NOW)
        self.assertEqual(result.route_id, 1)
        self.assertEqual(result.bus_minutes, 15)

    def test_the_board_rows_expected_time_is_the_one_it_plans_around(self):
        # Our prediction (9) says sooner than the feed's countdown (10): the
        # answer follows the board's one number, not the raw feed.
        result = ride([eta(10)], [eta(22)], ours=9, walk_to=6)
        self.assertEqual(result.bus_minutes, 9)
        self.assertEqual(result.bus_at, NOW + dt.timedelta(minutes=9))
        self.assertEqual(result.ride_minutes, 12)  # a duration: still the feed's gap

    def test_when_the_expected_time_is_too_soon_the_feeds_later_buses_follow_it(self):
        result = ride([eta(4, bus_id=1), eta(15, bus_id=2)], [eta(25, bus_id=2)], ours=3, walk_to=6)
        self.assertEqual(result.bus_minutes, 15)
        self.assertEqual(result.ride_minutes, 10)

    def test_a_prediction_with_no_feed_countdown_still_answers_but_not_the_ride(self):
        result = ride([], [], ours=12, walk_to=6)
        self.assertEqual(result.kind, answers.RIDE)
        self.assertEqual(result.bus_minutes, 12)
        self.assertIsNone(result.ride_minutes)

    def test_no_catchable_bus_says_so_and_what_walking_costs(self):
        result = ride([eta(2)], [], walk_to=6)
        self.assertEqual(result.kind, answers.NO_BUS)
        self.assertEqual(result.headline, "No bus to catch right now")
        self.assertEqual(result.detail, "Walking the whole way takes about 40 min.")

    def test_no_running_route_on_the_board_means_no_bus(self):
        with etas_by_stop([eta(10)], [eta(22)]):
            result = answers.answer(plan(), Board(running=False, rows=[]), 40, NOW)
        self.assertEqual(result.kind, answers.NO_BUS)

    def test_when_walking_is_no_longer_it_says_walk(self):
        result = answers.answer(plan(walk_to=5, walk_from=5, walk_all=8), BOARD, 8, NOW)
        self.assertEqual(result.kind, answers.WALK)
        self.assertEqual(result.headline, "Walk there: about 8 min")

    def test_no_plan_says_no_shuttle_links_the_places(self):
        result = answers.answer(None, None, 25, NOW)
        self.assertEqual(result.kind, answers.NO_ROUTE)
        self.assertEqual(result.headline, "No shuttle running now links these places")
        self.assertEqual(result.detail, "Walking the whole way takes about 25 min.")

    def test_clock_times_are_eastern_with_am_and_pm(self):
        self.assertEqual(answers.clock(NOW), "12:00 PM")
        self.assertEqual(answers.clock(NOW + dt.timedelta(hours=7, minutes=5)), "7:05 PM")
        self.assertEqual(answers.clock(NOW - dt.timedelta(hours=12)), "12:00 AM")


class RouteSegmentTests(SimpleTestCase):
    # A loop: four corners going round, [lat, lon] flattened as the feed has it.
    PATH = [41.30, -72.93, 41.31, -72.93, 41.31, -72.92, 41.30, -72.92]
    A = {"lat": 41.30, "lon": -72.93}
    B = {"lat": 41.31, "lon": -72.92}

    def test_the_ride_runs_from_one_stop_to_the_next_along_the_path(self):
        segment = answers.route_segment(self.PATH, self.A, self.B)
        self.assertEqual(segment, [(41.30, -72.93), (41.30, -72.93), (41.31, -72.93), (41.31, -72.92), (41.31, -72.92)])

    def test_a_stop_earlier_on_the_path_means_riding_round_the_loop(self):
        segment = answers.route_segment(self.PATH, self.B, self.A)
        self.assertEqual(segment[1:-1], [(41.31, -72.92), (41.30, -72.92), (41.30, -72.93)])

    def test_a_route_with_no_usable_path_has_no_segment(self):
        self.assertEqual(answers.route_segment([], self.A, self.B), [])
        self.assertEqual(answers.route_segment([41.30, -72.93], self.A, self.B), [])


def feed_by_stop(etas_by_stop, routes):
    """The test feed, but answering each stop's ETAs separately."""
    base = feed(routes=routes)

    def urlopen(request, timeout):
        parts = urlsplit(request.full_url)
        if parts.path != "/routes_eta.php":
            return base(request, timeout)
        response = mock.MagicMock()
        response.__enter__.return_value = response
        stop = parse_qs(parts.query)["stop"][0]
        response.read.return_value = json.dumps(etas_by_stop[stop]).encode()
        return response

    return urlopen


def feed_etas(stop, *etas):
    return {"etas": {stop: {"etas": [{"route": r, "bus_id": b, "bus_name": str(b), "avg": m} for r, b, m in etas]}}}


# Route 1 runs Science Hill (10), Grove (20), Union (30); the path is the stops'.
PATHED = [
    {**route, "path": [41.316, -72.922, 41.313, -72.925, 41.297, -72.926]} if route["id"] == 1 else route
    for route in ROUTES
]
TRIP = {"from": "Grove", "from_at": "41.3130,-72.9250", "to": "Union", "to_at": "41.2970,-72.9260"}


@mock.patch("transit.next_buses.predict_minutes", return_value=None)
@mock.patch("transit.downtowner.urlopen")
class TripPageAnswerTests(TestCase):
    def setUp(self):
        cache.clear()

    def stub(self, urlopen, at_grove, at_union):
        urlopen.side_effect = feed_by_stop({"20": feed_etas("20", *at_grove), "30": feed_etas("30", *at_union)}, PATHED)

    def test_the_answer_comes_before_everything_else_on_the_page(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        html = self.client.get(reverse("trip"), TRIP).content.decode()
        html = html[html.index("<main") :]
        answer = html.index("trip-answer-headline")
        for later in ("Grove to Union", 'data-trip-map', "trip-steps", "Next buses at"):
            self.assertLess(answer, html.index(later), later)

    def test_it_shows_the_ride_the_arrival_and_the_whole_trip(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        response = self.client.get(reverse("trip"), TRIP)
        answer = response.context["answer"]
        self.assertEqual(answer.kind, "ride")
        self.assertEqual(answer.ride_minutes, 12)
        self.assertContains(response, "<dd>12 min</dd>", html=True)
        self.assertContains(response, "Arrive at Union")

    def test_without_a_ride_time_it_says_so_instead_of_guessing(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [])
        response = self.client.get(reverse("trip"), TRIP)
        self.assertContains(response, "The feed doesn't say yet how long this ride takes.")
        self.assertNotContains(response, "Arrive at Union")

    def test_the_map_gets_the_places_the_stops_and_the_stretch_of_route(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        data = self.client.get(reverse("trip"), TRIP).context["map_data"]
        self.assertEqual(data["origin"], [41.313, -72.925])
        self.assertEqual(data["board"]["name"], "Grove Street")
        self.assertEqual(data["get_off"]["name"], "Union Station")
        self.assertEqual(data["route"]["color"], "4472C4")
        self.assertEqual(data["route"]["path"][0], [41.313, -72.925])
        self.assertEqual(data["route"]["path"][-1], [41.297, -72.926])

    def test_the_maps_data_is_in_the_page_for_its_script(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        self.assertContains(self.client.get(reverse("trip"), TRIP), '<script id="trip-map-data" type="application/json">')

    def test_with_no_bus_to_ride_the_map_still_shows_the_two_places(self, urlopen, predict):
        self.stub(urlopen, [], [])
        data = self.client.get(reverse("trip"), TRIP).context["map_data"]
        self.assertEqual(data["destination"], [41.297, -72.926])
        self.assertNotIn("route", data)


class RowExpectedTests(SimpleTestCase):
    """Row.expected is the one number the answer plans around."""

    def test_our_prediction_when_we_have_one_else_the_feeds_countdown_else_none(self):
        row = Row(route_id=1, name="Blue", label="Blue", color="4472C4", official=[10, 20], ours=9)
        self.assertEqual(row.expected, 9)
        row.ours = None
        self.assertEqual(row.expected, 10)
        row.official = []
        self.assertIsNone(row.expected)
